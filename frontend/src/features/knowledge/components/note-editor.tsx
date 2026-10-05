import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Bold,
  Check,
  Code,
  Heading2,
  Italic,
  Link as LinkIcon,
  List,
  Loader2,
  Quote,
  RotateCcw,
  Save,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { Input } from '@/components/ui/input'
import { useCreateNote, useUpdateNote } from '@/features/knowledge/hooks'
import { toApiError } from '@/services/errors'
import { cn } from '@/lib/utils'
import { formatRelative, renderMarkdown } from '@/types/knowledge'
import type { Note } from '@/types/knowledge'

export interface NoteEditorProps {
  /** Present to edit an existing note; absent to compose a new one. */
  note?: Note
  /** Fired with the note after every successful save, created or updated. */
  onSaved?: (note: Note) => void
  readOnly?: boolean
  /** Rendered beside the save state — the host's publish/archive controls. */
  actions?: React.ReactNode
  className?: string
}

type SaveState = 'saved' | 'saving' | 'unsaved' | 'error' | 'needs-title'

interface Draft {
  title: string
  content: string
  at: number
}

/**
 * Drafts are the safety net, not the store.
 *
 * A note is stored on the server, but a tab can be closed, a laptop can sleep,
 * and a save can fail — and the spec requires that none of those destroy the
 * user's words. Every keystroke that is not yet persisted is mirrored into
 * `localStorage` under a per-note key, and a failed save leaves it there. The
 * draft is cleared only once the server has the same text.
 */
const DRAFT_PREFIX = 'nexus.knowledge.note.draft.'

const AUTOSAVE_DEBOUNCE_MS = 800

function draftKey(noteId: string | null): string {
  return `${DRAFT_PREFIX}${noteId ?? 'new'}`
}

function readDraft(noteId: string | null): Draft | null {
  try {
    const raw = window.localStorage.getItem(draftKey(noteId))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const { title, content, at } = parsed as Partial<Draft>
    if (typeof title !== 'string' || typeof content !== 'string') return null
    return { title, content, at: typeof at === 'number' ? at : 0 }
  } catch {
    // A corrupt or unavailable draft is not worth an error: the server copy is
    // still there, and the editor falls back to it.
    return null
  }
}

function writeDraft(noteId: string | null, draft: Draft): void {
  try {
    window.localStorage.setItem(draftKey(noteId), JSON.stringify(draft))
  } catch {
    // Private browsing, or a full quota. The server copy is unaffected.
  }
}

function clearDraft(noteId: string | null): void {
  try {
    window.localStorage.removeItem(draftKey(noteId))
  } catch {
    // Nothing to do; the next successful save clears it again.
  }
}

interface FormatAction {
  label: string
  icon: LucideIcon
  /** Wraps the selection; `prefix` opens and `suffix` closes. */
  prefix: string
  suffix?: string
  placeholder?: string
  /** Inserts at the start of the current line instead of around the caret. */
  linePrefix?: string
}

const FORMAT_ACTIONS: FormatAction[] = [
  { label: 'Heading', icon: Heading2, prefix: '', linePrefix: '## ', placeholder: 'Heading' },
  { label: 'Bold', icon: Bold, prefix: '**', suffix: '**', placeholder: 'bold text' },
  { label: 'Italic', icon: Italic, prefix: '*', suffix: '*', placeholder: 'italic text' },
  { label: 'Link', icon: LinkIcon, prefix: '[', suffix: '](https://)', placeholder: 'link text' },
  { label: 'Code', icon: Code, prefix: '`', suffix: '`', placeholder: 'code' },
  { label: 'Bulleted list', icon: List, prefix: '', linePrefix: '- ', placeholder: 'list item' },
  { label: 'Quote', icon: Quote, prefix: '', linePrefix: '> ', placeholder: 'quoted text' },
]

const TEXTAREA_CLASSES =
  'h-full w-full resize-none rounded-md border border-input bg-background px-3 py-2 font-mono text-sm leading-relaxed shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/**
 * Markdown source editor with a live preview and a debounced autosave.
 *
 * Deliberately a styled `<textarea>` and not a WYSIWYG surface: no editor
 * library is installed, hand-rolling contenteditable is the thing the spec
 * warns against, and storing Markdown means the preview, the export and the
 * round trip are all free.
 *
 * The save state is explicit — `Saved` / `Saving…` / `Unsaved changes` /
 * `Needs a title` / `Unable to save` — because a note is the one surface where a
 * silent failure costs the user their work. Nothing here ever discards text: the
 * local draft outlives a failed save and a closed tab, and a restored draft says
 * so and offers to be thrown away.
 */
/**
 * Reads the server copy, or the local draft when that is the newer text.
 *
 * Called once per mount, from a `useState` initialiser — the editor loads the
 * note it was opened with and never re-seeds from props, so a refetch of the
 * same record cannot overwrite what the user is typing. A host that swaps the
 * note it is editing must remount the editor (`key={note.id}`), the same
 * discipline `ProjectFormDialog` uses.
 */
function initialText(note: Note | undefined): { title: string; content: string; recovered: boolean } {
  const serverText = { title: note?.title ?? '', content: note?.content ?? '' }
  const draft = readDraft(note?.id ?? null)
  const draftIsNewer =
    draft !== null &&
    (draft.title !== serverText.title || draft.content !== serverText.content) &&
    // Only a draft written after the record was last updated is the user's
    // work in progress; an older one is stale text, not something to restore.
    draft.at >= new Date(note?.updated_at ?? 0).getTime()

  return draftIsNewer && draft
    ? { title: draft.title, content: draft.content, recovered: true }
    : { ...serverText, recovered: false }
}

export function NoteEditor({ note, onSaved, readOnly = false, actions, className }: NoteEditorProps) {
  const initial = useMemo(() => initialText(note), [note])
  const [noteId, setNoteId] = useState<string | null>(note?.id ?? null)
  const [title, setTitle] = useState(initial.title)
  const [content, setContent] = useState(initial.content)
  const [state, setState] = useState<SaveState>(initial.recovered ? 'unsaved' : 'saved')
  const [recovered, setRecovered] = useState(initial.recovered)
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(note ? new Date(note.updated_at) : null)
  const [saveError, setSaveError] = useState<string | null>(null)

  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  /** The last text the server acknowledged; the autosave diffs against this. */
  const persistedRef = useRef<{ title: string; content: string }>({
    title: initial.title,
    content: initial.content,
  })
  const caretRef = useRef<{ start: number; end: number } | null>(null)
  const timerRef = useRef<number | null>(null)
  const savingRef = useRef(false)

  const createNote = useCreateNote()
  const updateNote = useUpdateNote()

  const save = useCallback(async () => {
    if (readOnly || savingRef.current) return
    const trimmedTitle = title.trim()
    // An empty title is a 422 on both create and update — `title` is
    // `min_length=1` — so a blank one makes every autosave a request that cannot
    // succeed. Writing the body first, or clearing the title of a saved note, is
    // an ordinary thing to do; neither should spend a request per keystroke to be
    // told so. The draft still holds the text and the state line says what is
    // missing.
    if (!trimmedTitle) {
      setState('needs-title')
      return
    }
    if (trimmedTitle === persistedRef.current.title && content === persistedRef.current.content) {
      return
    }

    savingRef.current = true
    setState('saving')
    try {
      const payload = { title: trimmedTitle, content }
      const saved = noteId
        ? await updateNote.mutateAsync({ id: noteId, payload })
        : await createNote.mutateAsync(payload)

      persistedRef.current = { title: saved.title, content: saved.content }
      setNoteId(saved.id)
      setState('saved')
      setSaveError(null)
      setLastSavedAt(new Date())
      clearDraft(noteId)
      if (!noteId) clearDraft(null)
      onSaved?.(saved)
    } catch (cause) {
      // The draft stays in localStorage and the state says so: the words are
      // recoverable and the next keystroke (or Ctrl+S) tries again.
      setState('error')
      setSaveError(toApiError(cause).message)
    } finally {
      savingRef.current = false
    }
  }, [content, createNote, noteId, onSaved, readOnly, title, updateNote])

  // Debounced autosave. The draft is written immediately — that write is local
  // and cheap, and it is what makes a closed tab survivable.
  useEffect(() => {
    if (readOnly) return
    if (title === persistedRef.current.title && content === persistedRef.current.content) return

    writeDraft(noteId, { title, content, at: Date.now() })
    setState(title.trim() === '' ? 'needs-title' : 'unsaved')

    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      void save()
    }, AUTOSAVE_DEBOUNCE_MS)

    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    }
  }, [content, noteId, readOnly, save, title])

  // Ctrl/Cmd+S saves immediately rather than waiting out the debounce.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (timerRef.current !== null) {
          window.clearTimeout(timerRef.current)
          timerRef.current = null
        }
        void save()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [save])

  // Caret restoration after a toolbar insert, applied once the value has landed.
  useEffect(() => {
    const caret = caretRef.current
    const element = textareaRef.current
    if (!caret || !element) return
    caretRef.current = null
    element.focus()
    element.setSelectionRange(caret.start, caret.end)
  }, [content])

  const preview = useMemo(
    () =>
      // `renderMarkdown` escapes the source before emitting any tag, so this
      // is safe by construction rather than by a blocklist — see its docstring.
      renderMarkdown(content),
    [content],
  )

  function applyFormat(action: FormatAction) {
    const element = textareaRef.current
    if (!element) return
    const start = element.selectionStart
    const end = element.selectionEnd

    if (action.linePrefix) {
      const lineStart = content.lastIndexOf('\n', start - 1) + 1
      caretRef.current = { start: lineStart + action.linePrefix.length, end }
      setContent(content.slice(0, lineStart) + action.linePrefix + content.slice(lineStart))
      return
    }

    const selected = content.slice(start, end) || (action.placeholder ?? '')
    const suffix = action.suffix ?? action.prefix
    caretRef.current = { start: start + action.prefix.length, end: start + action.prefix.length + selected.length }
    setContent(content.slice(0, start) + action.prefix + selected + suffix + content.slice(end))
  }

  const wordCount = content.trim() === '' ? 0 : content.trim().split(/\s+/).length

  return (
    <div className={cn('flex min-h-0 flex-col gap-3', className)}>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={title}
          readOnly={readOnly}
          maxLength={300}
          placeholder="Untitled note"
          aria-label="Note title"
          onChange={(event) => setTitle(event.target.value)}
          className="h-10 max-w-xl text-base font-medium"
        />

        <div className="flex items-center gap-2" aria-live="polite">
          {state === 'saving' && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
              Saving…
            </span>
          )}
          {state === 'unsaved' && (
            <span className="flex items-center gap-1.5 text-xs text-warning">
              <Save aria-hidden="true" className="size-3.5" />
              Unsaved changes
            </span>
          )}
          {state === 'needs-title' && (
            <span className="flex items-center gap-1.5 text-xs text-warning">
              <Save aria-hidden="true" className="size-3.5" />
              Needs a title
            </span>
          )}
          {state === 'saved' && (
            <span className="flex items-center gap-1.5 text-xs text-success">
              <Check aria-hidden="true" className="size-3.5" />
              Saved
            </span>
          )}
          {state === 'error' && (
            <span className="flex items-center gap-1.5 text-xs font-medium text-destructive">
              <AlertTriangle aria-hidden="true" className="size-3.5" />
              Unable to save
            </span>
          )}

          {actions}
        </div>
      </div>

      {state === 'error' && saveError && (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/[0.04] px-3 py-2 text-xs leading-relaxed text-destructive">
          {saveError} Your text is held in a local draft on this device — press{' '}
          <Kbd>Ctrl</Kbd> + <Kbd>S</Kbd> to try again.
        </p>
      )}

      {state === 'needs-title' && (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs leading-relaxed text-warning">
          A note needs a title before it can be saved — the backend rejects a blank one, so
          nothing is sent until you give it one. Your text is held in a local draft on this
          device in the meantime.
        </p>
      )}

      {recovered && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2">
          <p className="text-xs leading-relaxed text-warning">
            A local draft from this device was restored — it was newer than the saved copy.
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setTitle(persistedRef.current.title)
              setContent(persistedRef.current.content)
              setRecovered(false)
              setState('saved')
              clearDraft(noteId)
            }}
          >
            <RotateCcw aria-hidden="true" className="size-3.5" />
            Discard draft
          </Button>
        </div>
      )}

      <div
        className="flex flex-wrap items-center gap-1 rounded-md border border-border bg-card p-1"
        role="toolbar"
        aria-label="Markdown formatting"
      >
        {FORMAT_ACTIONS.map((action) => (
          <Button
            key={action.label}
            type="button"
            variant="ghost"
            size="icon"
            className="size-8 text-muted-foreground"
            disabled={readOnly}
            title={action.label}
            onClick={() => applyFormat(action)}
          >
            <action.icon aria-hidden="true" className="size-4" />
            <span className="sr-only">{action.label}</span>
          </Button>
        ))}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-8 text-muted-foreground"
          disabled={readOnly}
          title="Save now"
          onClick={() => void save()}
        >
          <Save aria-hidden="true" className="size-4" />
          <span className="sr-only">Save now</span>
        </Button>

        <span className="ml-auto pr-2 text-xs text-muted-foreground">
          {wordCount} {wordCount === 1 ? 'word' : 'words'} · Markdown
          {lastSavedAt && (
            <>
              {' · saved '}
              {formatRelative(lastSavedAt.toISOString())}
            </>
          )}
        </span>
      </div>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-2">
        <textarea
          ref={textareaRef}
          value={content}
          readOnly={readOnly}
          spellCheck
          placeholder="Write in Markdown. The preview updates as you type."
          aria-label="Note content (Markdown)"
          onChange={(event) => setContent(event.target.value)}
          className={TEXTAREA_CLASSES}
        />

        <div
          className="min-h-0 space-y-2 overflow-y-auto rounded-md border border-border bg-card p-4 text-sm leading-relaxed text-foreground"
          // Safe by construction: the renderer escapes the source before it
          // emits any tag and refuses a non-http(s) href.
          dangerouslySetInnerHTML={{ __html: preview || '<p class="text-muted-foreground">Nothing to preview yet.</p>' }}
        />
      </div>
    </div>
  )
}
