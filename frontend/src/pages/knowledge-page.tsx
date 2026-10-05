import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowDownUp, Archive, Plus, Search, Trash2, X } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
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
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  BookmarkFormDialog,
  ConceptFormDialog,
  EmptyKnowledge,
  GraphView,
  KnowledgeSearchBar,
  NoteCard,
  NoteEditor,
  ResourceTypeBadge,
} from '@/features/knowledge/components'
import {
  useBookmarks,
  useConcepts,
  useCreateResource,
  useDeleteBookmark,
  useDeleteConcept,
  useDeleteResource,
  useNotes,
  useResources,
} from '@/features/knowledge/hooks'
import { useDebouncedValue } from '@/hooks/use-debounce'
import { ConfirmDialog } from '@/features/work/components'
import { useTags } from '@/features/work/hooks'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import {
  BOOKMARK_SORT_KEYS,
  CONCEPT_SORT_KEYS,
  KNOWLEDGE_ENTITY_TYPES,
  KNOWLEDGE_ENTITY_META,
  MAX_PAGE_SIZE,
  NOTE_SORT_KEYS,
  NOTE_STATUSES,
  NOTE_STATUS_META,
  RESOURCE_SORT_KEYS,
  RESOURCE_TYPES,
  RESOURCE_TYPE_META,
  SORT_LABELS,
  formatRelative,
} from '@/types/knowledge'
import type {
  Bookmark,
  Concept,
  KnowledgeEntityType,
  Note,
  NoteStatus,
  Resource,
  ResourceCreatePayload,
  ResourceType,
} from '@/types/knowledge'
import type { SortOrder } from '@/types/work'

const PAGE_SIZE = 25
/** Ceiling of the client-side tag/date filters, which run over one window. */
const CLIENT_FILTER_WINDOW = MAX_PAGE_SIZE

const LIST_TABS = ['notes', 'concepts', 'resources', 'bookmarks'] as const
const TABS = [...LIST_TABS, 'graph'] as const

type ListTab = (typeof LIST_TABS)[number]
type Tab = (typeof TABS)[number]

function isTab(value: string | null): value is Tab {
  return value !== null && (TABS as readonly string[]).includes(value)
}

/**
 * Each list has its own default sort, chosen to match the backend's: notes and
 * resources are feeds (most recently touched first), concepts and categories are
 * indexes a person scans alphabetically. Sending an explicit value everywhere
 * would be fine; not sending the endpoint's own default keeps the URL short.
 */
const TAB_DEFAULTS: Record<ListTab, { sort: string; order: SortOrder }> = {
  notes: { sort: 'updated_at', order: 'desc' },
  concepts: { sort: 'name', order: 'asc' },
  resources: { sort: 'updated_at', order: 'desc' },
  bookmarks: { sort: 'created_at', order: 'desc' },
}

const TAB_SORTS: Record<ListTab, readonly string[]> = {
  notes: NOTE_SORT_KEYS,
  concepts: CONCEPT_SORT_KEYS,
  resources: RESOURCE_SORT_KEYS,
  bookmarks: BOOKMARK_SORT_KEYS,
}

/**
 * Resolves the URL's `sort` against the allowlist of the tab actually open.
 *
 * `sort` is one parameter shared by four lists with four different allowlists —
 * `name` orders concepts and is a 422 for notes, resources and bookmarks. A value
 * that was valid where it was chosen, or that arrived on a shared or stale link,
 * must not be sent on faith: the service answers an unknown key with a 422 no
 * retry can clear. Falling back to the tab's own default narrows the request
 * instead of breaking it, which is what every other URL value here does.
 */
function resolveSort(tab: ListTab, requested: string | undefined): string {
  if (requested && (TAB_SORTS[tab] as readonly string[]).includes(requested)) return requested
  return TAB_DEFAULTS[tab].sort
}

/**
 * The URL is the source of truth for every piece of view state — tab, filters,
 * sort, page — so a filtered browser survives a refresh and can be shared.
 * Values are resolved against the same allowlists the backend validates
 * against: a stale bookmark should narrow the list, not 422 it.
 */
interface KnowledgeView {
  tab: Tab
  q: string
  status?: NoteStatus
  resourceType?: ResourceType
  tags: string[]
  after?: string
  before?: string
  sort?: string
  order?: SortOrder
  page: number
  graphType?: KnowledgeEntityType
  graphLimit: number
}

function parseView(params: URLSearchParams): KnowledgeView {
  const tab = isTab(params.get('tab')) ? (params.get('tab') as Tab) : 'notes'
  const status = params.get('status')
  const resourceType = params.get('type')
  const graphType = params.get('gtype')
  const sort = params.get('sort') ?? undefined
  const order = params.get('order')
  const page = Number.parseInt(params.get('page') ?? '1', 10)
  const graphLimit = Number.parseInt(params.get('glimit') ?? '', 10)

  return {
    tab,
    q: params.get('q') ?? '',
    status: NOTE_STATUSES.includes(status as NoteStatus) ? (status as NoteStatus) : undefined,
    resourceType: RESOURCE_TYPES.includes(resourceType as ResourceType)
      ? (resourceType as ResourceType)
      : undefined,
    tags: (params.get('tags') ?? '').split(',').filter(Boolean),
    after: params.get('after') || undefined,
    before: params.get('before') || undefined,
    sort: sort || undefined,
    order: order === 'asc' ? 'asc' : order === 'desc' ? 'desc' : undefined,
    page: Number.isFinite(page) && page > 1 ? page : 1,
    graphType: KNOWLEDGE_ENTITY_TYPES.includes(graphType as KnowledgeEntityType)
      ? (graphType as KnowledgeEntityType)
      : undefined,
    graphLimit: Number.isFinite(graphLimit) && graphLimit > 0 ? graphLimit : 200,
  }
}

function writeView(view: KnowledgeView): URLSearchParams {
  const params = new URLSearchParams()
  if (view.tab !== 'notes') params.set('tab', view.tab)
  if (view.q) params.set('q', view.q)
  if (view.status) params.set('status', view.status)
  if (view.resourceType) params.set('type', view.resourceType)
  if (view.tags.length) params.set('tags', view.tags.join(','))
  if (view.after) params.set('after', view.after)
  if (view.before) params.set('before', view.before)
  if (view.sort) params.set('sort', view.sort)
  if (view.order) params.set('order', view.order)
  if (view.page > 1) params.set('page', String(view.page))
  if (view.graphType) params.set('gtype', view.graphType)
  if (view.graphLimit !== 200) params.set('glimit', String(view.graphLimit))
  return params
}

function StatTile({ label, value, loading }: { label: string; value?: number; loading?: boolean }) {
  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card px-3 py-2">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="mt-2 h-6 w-10" />
      </div>
    )
  }
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      {/* `meta.total` is the size of the filtered set on the server, never a
          number this page made up. */}
      <p className="text-lg font-semibold tabular-nums text-foreground">{value ?? 0}</p>
    </div>
  )
}

interface EntityRowProps {
  title: string
  to: string
  subtitle?: string | null
  badge?: ReactNode
  updatedAt: string
  onDelete?: () => void
  deleteLabel: string
}

function EntityRow({
  title,
  to,
  subtitle,
  badge,
  updatedAt,
  onDelete,
  deleteLabel,
}: EntityRowProps) {
  return (
    <li className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 transition-colors hover:bg-accent/40">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Link to={to} className="truncate text-sm font-medium text-foreground hover:underline">
            {title}
          </Link>
          {badge}
        </div>
        {subtitle ? (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
        {formatRelative(updatedAt)}
      </span>
      {onDelete ? (
        <Button
          variant="ghost"
          size="icon"
          className="size-8 shrink-0 text-muted-foreground hover:text-destructive"
          onClick={onDelete}
        >
          <Trash2 aria-hidden="true" />
          <span className="sr-only">{deleteLabel}</span>
        </Button>
      ) : null}
    </li>
  )
}

function RowSkeleton() {
  return (
    <li className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2">
      <Skeleton className="h-3.5 flex-1" />
      <Skeleton className="h-4 w-16" />
      <Skeleton className="h-3 w-20" />
    </li>
  )
}

function Pager({
  page,
  total,
  onPage,
}: {
  page: number
  total: number
  onPage: (next: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  if (total <= PAGE_SIZE) return null
  return (
    <nav className="mt-4 flex items-center justify-between gap-3" aria-label="Knowledge pages">
      <p className="text-xs text-muted-foreground">
        Page {page} of {pages} · {total} matching
      </p>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          Next
        </Button>
      </div>
    </nav>
  )
}

/**
 * Resources have no dedicated form component, so the browser owns this one.
 *
 * **It is still a real `<form>`.** Enter in any field submits, which is the one
 * keyboard path a dialog that only hangs `onClick` off a button denies its
 * reader, and a `<form>` is also what the browser uses to move the submit button
 * into an implicit submission. `noValidate` because the fields are already
 * bounded by `maxLength` and the server is the authority on the URL.
 */
function ResourceDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (resource: Resource) => void
}) {
  const create = useCreateResource()
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [resourceType, setResourceType] = useState<ResourceType>('article')

  /** All four fields, the type included: a half-typed resource is not a draft. */
  function reset() {
    setTitle('')
    setUrl('')
    setDescription('')
    setResourceType('article')
  }

  async function submit() {
    const payload: ResourceCreatePayload = {
      title: title.trim(),
      // Only http(s) survives the backend's validator; an empty field is simply
      // omitted rather than sent as "".
      url: url.trim() || null,
      description: description.trim() || null,
      resource_type: resourceType,
    }
    try {
      const saved = await create.mutateAsync(payload)
      toast.success('Resource filed', saved.title)
      onSaved(saved)
      reset()
      onOpenChange(false)
    } catch (cause) {
      toast.error('Could not file that resource', toApiError(cause).message)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New resource</DialogTitle>
          <DialogDescription>
            An external thing worth citing. Nothing is fetched — the URL is stored as you type it.
          </DialogDescription>
        </DialogHeader>
        <form
          className="app-form-stack"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
          noValidate
        >
          <div className="app-form-field">
            <Label htmlFor="resource-title">Title</Label>
            <Input
              id="resource-title"
              value={title}
              maxLength={300}
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="app-form-field">
            <Label htmlFor="resource-url">URL</Label>
            <Input
              id="resource-url"
              type="url"
              placeholder="https://"
              value={url}
              maxLength={2048}
              onChange={(event) => setUrl(event.target.value)}
            />
            <p className="app-form-hint">Must be an absolute http or https URL.</p>
          </div>
          <div className="app-form-field">
            <Label htmlFor="resource-type">Type</Label>
            <Select
              id="resource-type"
              value={resourceType}
              onChange={(event) => setResourceType(event.target.value as ResourceType)}
            >
              {RESOURCE_TYPES.map((type) => (
                <option key={type} value={type}>
                  {RESOURCE_TYPE_META[type].label}
                </option>
              ))}
            </Select>
          </div>
          <div className="app-form-field">
            <Label htmlFor="resource-description">Description</Label>
            <textarea
              id="resource-description"
              rows={3}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                reset()
                onOpenChange(false)
              }}
              disabled={create.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending || !title.trim()}>
              {create.isPending ? 'Filing…' : 'File resource'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default function KnowledgePage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const view = useMemo(() => parseView(searchParams), [searchParams])

  const [searchDraft, setSearchDraft] = useState(view.q)
  const [composerOpen, setComposerOpen] = useState(false)
  const [conceptOpen, setConceptOpen] = useState(false)
  const [resourceOpen, setResourceOpen] = useState(false)
  const [bookmarkOpen, setBookmarkOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<
    { kind: 'bookmark' | 'resource' | 'concept'; id: string; title: string } | null
  >(null)

  /**
   * `view.q` is the URL, and the input is not its only writer: "Clear filters",
   * a tag chip, a shared link and the browser's own Back/Forward all go through
   * `apply` and none of them touch this draft. Without this the box kept showing
   * a query the list was no longer filtered by. Typing still sets both in the
   * same event, so the two never disagree — and this only re-seeds when the URL
   * actually moved, not on every keystroke.
   */
  const [lastQ, setLastQ] = useState(view.q)
  if (lastQ !== view.q) {
    setLastQ(view.q)
    setSearchDraft(view.q)
  }

  // Typing replaces the current entry rather than pushing one per keystroke —
  // five search characters should not cost five presses of the back button.
  const apply = useCallback(
    (patch: Partial<KnowledgeView>, resetPage = true, replace = false) => {
      const next: KnowledgeView = { ...view, ...patch }
      if (resetPage) next.page = 1
      setSearchParams(writeView(next), { replace })
    },
    [setSearchParams, view],
  )

  const tab: ListTab = view.tab === 'graph' ? 'notes' : view.tab
  const defaults = TAB_DEFAULTS[tab]
  const sort = resolveSort(tab, view.sort)
  const order = view.order ?? defaults.order
  const debouncedQ = useDebouncedValue(view.q, 300)

  const offset = (view.page - 1) * PAGE_SIZE
  const notesQuery = useNotes({
    limit: PAGE_SIZE,
    offset,
    status: view.status,
    search: debouncedQ || undefined,
    sort,
    order,
  })
  const conceptsQuery = useConcepts({
    limit: PAGE_SIZE,
    offset,
    search: debouncedQ || undefined,
    sort,
    order,
  })
  const resourcesQuery = useResources({
    limit: PAGE_SIZE,
    offset,
    search: debouncedQ || undefined,
    sort,
    order,
  })
  const bookmarksQuery = useBookmarks({
    limit: PAGE_SIZE,
    offset,
    search: debouncedQ || undefined,
    sort,
    order,
  })

  // Counts for the dashboard. One row each: `meta.total` is the size of the
  // filtered set, which with no filter is the size of the whole collection.
  const counts = {
    notes: useNotes({ limit: 1 }),
    concepts: useConcepts({ limit: 1 }),
    resources: useResources({ limit: 1 }),
    bookmarks: useBookmarks({ limit: 1 }),
  }

  // Most-used tags are counted from the notes this page has already loaded
  // rather than fetched: there is no endpoint that ranks tags by note usage,
  // and inventing one from `WorkTag.task_count` would be counting a different
  // question.
  const tagWindow = useNotes({ limit: CLIENT_FILTER_WINDOW, sort: 'updated_at', order: 'desc' })
  const { data: tagPage } = useTags({ limit: MAX_PAGE_SIZE })
  // Five rows out of the same window the tag counts come from, so the dashboard
  // costs no extra request for its "recently updated" panel.
  const recent = useMemo(
    () => (tagWindow.data?.items ?? []).slice(0, 5),
    [tagWindow.data],
  )

  const tagNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const tag of tagPage?.items ?? []) map.set(tag.id, tag.name)
    return map
  }, [tagPage])

  const tagUsage = useMemo(() => {
    const countsByTag = new Map<string, number>()
    for (const note of tagWindow.data?.items ?? []) {
      for (const id of note.tag_ids) countsByTag.set(id, (countsByTag.get(id) ?? 0) + 1)
    }
    return [...countsByTag.entries()]
      .map(([id, count]) => ({ id, name: tagNames.get(id) ?? 'Unknown tag', count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 8)
  }, [tagWindow.data, tagNames])

  const activeList =
    tab === 'notes'
      ? notesQuery
      : tab === 'concepts'
        ? conceptsQuery
        : tab === 'resources'
          ? resourcesQuery
          : bookmarksQuery

  const listItems = useMemo(() => (activeList.data?.items ?? []) as unknown[], [activeList.data])
  const total = activeList.data?.meta.total ?? 0

  const filtering =
    Boolean(view.status) ||
    Boolean(view.resourceType) ||
    view.tags.length > 0 ||
    Boolean(view.after) ||
    Boolean(view.before) ||
    Boolean(debouncedQ)

  /**
   * Only notes and concepts carry `tag_ids`; a resource or a bookmark row has no
   * such column, so a tag pressed on those tabs would empty the list rather than
   * narrow it. The chips are hidden there for the same reason.
   */
  const tagsApply = tab === 'notes' || tab === 'concepts'

  /**
   * Status, search and sort are the endpoint's own filters. **Resource type, tag
   * and date range are not** — `GET /knowledge/resources` declares no
   * `resource_type` (the openapi parameter list is `limit, offset, search,
   * sort, order`) and `GET /knowledge/notes` declares neither `tag_ids` nor
   * `updated_after`. FastAPI ignores an undeclared parameter rather than
   * 422-ing it, so sending them would silently return the unfiltered list while
   * the control claimed to be narrowing it. They are applied here over the
   * fetched window instead, and the notice below the filter bar says so
   * whenever one is narrowing the view.
   */
  const clientFilterActive =
    (tagsApply && view.tags.length > 0) ||
    Boolean(view.resourceType) ||
    Boolean(view.after) ||
    Boolean(view.before)
  const visible = useMemo(() => {
    if (!clientFilterActive) return listItems
    return listItems.filter((item) => {
      const record = item as {
        tag_ids?: string[]
        resource_type?: ResourceType
        updated_at?: string
        archived_at?: string | null
        created_at?: string
      }
      if (tagsApply && view.tags.length > 0) {
        const ids = record.tag_ids ?? []
        if (!view.tags.every((id) => ids.includes(id))) return false
      }
      if (view.resourceType && record.resource_type !== view.resourceType) return false
      const stamp = record.archived_at ?? record.updated_at ?? record.created_at
      if (stamp) {
        if (view.after && stamp.slice(0, 10) < view.after) return false
        if (view.before && stamp.slice(0, 10) > view.before) return false
      }
      return true
    })
  }, [
    clientFilterActive,
    listItems,
    tagsApply,
    view.tags,
    view.resourceType,
    view.after,
    view.before,
  ])

  const deleteBookmark = useDeleteBookmark()
  const deleteResource = useDeleteResource()
  const deleteConcept = useDeleteConcept()

  function openNote(id: string) {
    navigate(`/knowledge/notes/${id}`)
  }

  function confirmDelete() {
    if (!pendingDelete) return
    const request =
      pendingDelete.kind === 'bookmark'
        ? deleteBookmark.mutateAsync(pendingDelete.id)
        : pendingDelete.kind === 'concept'
          ? deleteConcept.mutateAsync(pendingDelete.id)
          : deleteResource.mutateAsync(pendingDelete.id)
    const noun =
      pendingDelete.kind === 'bookmark'
        ? 'Bookmark'
        : pendingDelete.kind === 'concept'
          ? 'Concept'
          : 'Resource'
    request
      .then(() => {
        toast.success(`${noun} deleted`, pendingDelete.title)
        setPendingDelete(null)
      })
      .catch((cause: unknown) => {
        toast.error('Could not delete that', toApiError(cause).message)
      })
  }

  const actions = (
    <>
      {tab === 'notes' && (
        <Button onClick={() => setComposerOpen(true)}>
          <Plus aria-hidden="true" />
          New note
        </Button>
      )}
      {tab === 'concepts' && (
        <Button onClick={() => setConceptOpen(true)}>
          <Plus aria-hidden="true" />
          New concept
        </Button>
      )}
      {tab === 'resources' && (
        <Button onClick={() => setResourceOpen(true)}>
          <Plus aria-hidden="true" />
          New resource
        </Button>
      )}
      {tab === 'bookmarks' && (
        <Button onClick={() => setBookmarkOpen(true)}>
          <Plus aria-hidden="true" />
          New bookmark
        </Button>
      )}
    </>
  )

  return (
    <div className="app-container space-y-6 py-6">
      <PageHeader
        title="Knowledge"
        description="Notes, concepts, resources and bookmarks, and the edges between them. The tab, filters and sort live in the address bar, so any view here can be linked to."
        actions={actions}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Notes" value={counts.notes.data?.meta.total} loading={counts.notes.isPending} />
        <StatTile
          label="Concepts"
          value={counts.concepts.data?.meta.total}
          loading={counts.concepts.isPending}
        />
        <StatTile
          label="Resources"
          value={counts.resources.data?.meta.total}
          loading={counts.resources.isPending}
        />
        <StatTile
          label="Bookmarks"
          value={counts.bookmarks.data?.meta.total}
          loading={counts.bookmarks.isPending}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <KnowledgeSearchBar placeholder="Search notes, concepts, resources and bookmarks" />
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <h2 className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Most-used tags
          </h2>
          {tagWindow.isPending ? (
            <div className="mt-2 space-y-1.5">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-3 w-24" />
            </div>
          ) : tagUsage.length === 0 ? (
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Not enough data yet — tags appear here once notes carry them.
            </p>
          ) : (
            <ul className="mt-2 space-y-1">
              {tagUsage.map((tag) => (
                <li key={tag.id} className="flex items-center justify-between gap-2 text-sm">
                  <button
                    type="button"
                    className="truncate text-foreground hover:underline"
                    onClick={() => apply({ tags: [tag.id] })}
                  >
                    {tag.name}
                  </button>
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {tag.count} {tag.count === 1 ? 'note' : 'notes'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-3">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="app-form-field">
            <Label htmlFor="knowledge-search">Search this tab</Label>
            <div className="relative">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                id="knowledge-search"
                type="search"
                className="pl-8"
                value={searchDraft}
                placeholder="Title or text"
                onChange={(event) => {
                  setSearchDraft(event.target.value)
                  apply({ q: event.target.value }, true, true)
                }}
              />
            </div>
          </div>

          {tab === 'notes' && (
            <div className="app-form-field">
              <Label htmlFor="knowledge-status">Status</Label>
              <Select
                id="knowledge-status"
                value={view.status ?? ''}
                onChange={(event) =>
                  apply({ status: (event.target.value || undefined) as NoteStatus | undefined })
                }
              >
                <option value="">All statuses</option>
                {NOTE_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {NOTE_STATUS_META[status].label}
                  </option>
                ))}
              </Select>
            </div>
          )}

          {tab === 'resources' && (
            <div className="app-form-field">
              <Label htmlFor="knowledge-resource-type">Type</Label>
              <Select
                id="knowledge-resource-type"
                value={view.resourceType ?? ''}
                onChange={(event) =>
                  apply({
                    resourceType: (event.target.value || undefined) as ResourceType | undefined,
                  })
                }
              >
                <option value="">All types</option>
                {RESOURCE_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {RESOURCE_TYPE_META[type].label}
                  </option>
                ))}
              </Select>
            </div>
          )}

          <div className="app-form-field">
            <Label htmlFor="knowledge-after">Changed from</Label>
            <Input
              id="knowledge-after"
              type="date"
              value={view.after ?? ''}
              onChange={(event) => apply({ after: event.target.value || undefined })}
            />
          </div>

          <div className="app-form-field">
            <Label htmlFor="knowledge-sort">Sort</Label>
            <div className="flex gap-2">
              <Select
                id="knowledge-sort"
                value={sort}
                onChange={(event) => apply({ sort: event.target.value })}
                disabled={view.tab === 'graph'}
              >
                {TAB_SORTS[tab].map((key) => (
                  <option key={key} value={key}>
                    {SORT_LABELS[key] ?? key}
                  </option>
                ))}
              </Select>
              <Button
                variant="outline"
                size="icon"
                className="shrink-0"
                aria-label={order === 'desc' ? 'Sorted descending' : 'Sorted ascending'}
                onClick={() => apply({ order: order === 'desc' ? 'asc' : 'desc' })}
                disabled={view.tab === 'graph'}
              >
                <ArrowDownUp aria-hidden="true" />
              </Button>
            </div>
          </div>
        </div>

        {tagsApply && tagPage && tagPage.items.length > 0 && (
          <fieldset className="mt-3 space-y-1.5">
            <legend className="text-xs font-medium text-muted-foreground">
              Tags — a note must carry every tag selected
            </legend>
            <div className="flex flex-wrap gap-1.5">
              {tagPage.items.slice(0, 20).map((tag) => {
                const active = view.tags.includes(tag.id)
                return (
                  <Button
                    key={tag.id}
                    type="button"
                    size="sm"
                    variant={active ? 'default' : 'outline'}
                    aria-pressed={active}
                    onClick={() =>
                      apply({
                        tags: active
                          ? view.tags.filter((id) => id !== tag.id)
                          : [...view.tags, tag.id],
                      })
                    }
                  >
                    {tag.name}
                  </Button>
                )
              })}
            </div>
          </fieldset>
        )}

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {activeList.isPending
              ? 'Loading…'
              : clientFilterActive
                ? `${visible.length} of ${total} shown — type, tag and date filters run over the ${PAGE_SIZE} rows on this page`
                : `${total} ${tab.replace(/s$/, '')}${total === 1 ? '' : 's'}`}
          </p>
          {filtering ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                apply({
                  q: '',
                  status: undefined,
                  resourceType: undefined,
                  tags: [],
                  after: undefined,
                  before: undefined,
                  sort: undefined,
                  order: undefined,
                })
              }
            >
              <X aria-hidden="true" />
              Clear filters
            </Button>
          ) : null}
        </div>
      </div>

      <Tabs
        value={view.tab}
        // The sort is dropped with the tab: leaving it behind would ask the next
        // list for an ordering it does not have, which is a 422 rather than a
        // different order. Each tab resolves its own default instead.
        onValueChange={(next) => apply({ tab: next as Tab, sort: undefined })}
      >
        <TabsList>
          <TabsTrigger value="notes">Notes</TabsTrigger>
          <TabsTrigger value="concepts">Concepts</TabsTrigger>
          <TabsTrigger value="resources">Resources</TabsTrigger>
          <TabsTrigger value="bookmarks">Bookmarks</TabsTrigger>
          <TabsTrigger value="graph">Graph</TabsTrigger>
        </TabsList>

        <TabsContent value="notes">
          {notesQuery.isPending ? (
            <ul className="space-y-2">
              {Array.from({ length: 6 }, (_, index) => (
                <RowSkeleton key={index} />
              ))}
            </ul>
          ) : notesQuery.isError ? (
            <ErrorState
              error={toApiError(notesQuery.error)}
              onRetry={() => void notesQuery.refetch()}
            />
          ) : visible.length === 0 ? (
            <EmptyKnowledge
              kind={filtering ? 'matches' : 'notes'}
              action={
                filtering ? undefined : (
                  <Button onClick={() => setComposerOpen(true)}>
                    <Plus aria-hidden="true" />
                    New note
                  </Button>
                )
              }
            />
          ) : (
            <>
              <ul className="space-y-2">
                {(visible as Note[]).map((note) => (
                  <li key={note.id}>
                    <NoteCard note={note} compact onOpen={(value) => openNote(value.id)} />
                  </li>
                ))}
              </ul>
              <Pager
                page={view.page}
                total={total}
                onPage={(next) => apply({ page: next }, false)}
              />
            </>
          )}
        </TabsContent>

        <TabsContent value="concepts">
          {conceptsQuery.isPending ? (
            <ul className="space-y-2">
              {Array.from({ length: 6 }, (_, index) => (
                <RowSkeleton key={index} />
              ))}
            </ul>
          ) : conceptsQuery.isError ? (
            <ErrorState
              error={toApiError(conceptsQuery.error)}
              onRetry={() => void conceptsQuery.refetch()}
            />
          ) : visible.length === 0 ? (
            <EmptyKnowledge
              kind={filtering ? 'matches' : 'concepts'}
              action={
                filtering ? undefined : (
                  <Button onClick={() => setConceptOpen(true)}>
                    <Plus aria-hidden="true" />
                    New concept
                  </Button>
                )
              }
            />
          ) : (
            <>
              <ul className="space-y-2">
                {(visible as Concept[]).map((concept) => (
                  <EntityRow
                    key={concept.id}
                    title={concept.name}
                    to={`/knowledge/concepts/${concept.id}`}
                    subtitle={concept.description}
                    updatedAt={concept.updated_at}
                    deleteLabel={`Delete ${concept.name}`}
                    onDelete={() =>
                      setPendingDelete({ kind: 'concept', id: concept.id, title: concept.name })
                    }
                  />
                ))}
              </ul>
              <Pager
                page={view.page}
                total={total}
                onPage={(next) => apply({ page: next }, false)}
              />
            </>
          )}
        </TabsContent>

        <TabsContent value="resources">
          {resourcesQuery.isPending ? (
            <ul className="space-y-2">
              {Array.from({ length: 6 }, (_, index) => (
                <RowSkeleton key={index} />
              ))}
            </ul>
          ) : resourcesQuery.isError ? (
            <ErrorState
              error={toApiError(resourcesQuery.error)}
              onRetry={() => void resourcesQuery.refetch()}
            />
          ) : visible.length === 0 ? (
            <EmptyKnowledge
              kind={filtering ? 'matches' : 'resources'}
              action={
                filtering ? undefined : (
                  <Button onClick={() => setResourceOpen(true)}>
                    <Plus aria-hidden="true" />
                    New resource
                  </Button>
                )
              }
            />
          ) : (
            <>
              <ul className="space-y-2">
                {(visible as Resource[]).map((resource) => (
                  <EntityRow
                    key={resource.id}
                    title={resource.title}
                    to={`/knowledge?tab=resources&q=${encodeURIComponent(resource.url ?? resource.title)}`}
                    subtitle={resource.url}
                    badge={<ResourceTypeBadge type={resource.resource_type} size="sm" />}
                    updatedAt={resource.updated_at}
                    deleteLabel={`Delete ${resource.title}`}
                    onDelete={() =>
                      setPendingDelete({ kind: 'resource', id: resource.id, title: resource.title })
                    }
                  />
                ))}
              </ul>
              <Pager
                page={view.page}
                total={total}
                onPage={(next) => apply({ page: next }, false)}
              />
            </>
          )}
        </TabsContent>

        <TabsContent value="bookmarks">
          {bookmarksQuery.isPending ? (
            <ul className="space-y-2">
              {Array.from({ length: 6 }, (_, index) => (
                <RowSkeleton key={index} />
              ))}
            </ul>
          ) : bookmarksQuery.isError ? (
            <ErrorState
              error={toApiError(bookmarksQuery.error)}
              onRetry={() => void bookmarksQuery.refetch()}
            />
          ) : visible.length === 0 ? (
            <EmptyKnowledge
              kind={filtering ? 'matches' : 'bookmarks'}
              action={
                filtering ? undefined : (
                  <Button onClick={() => setBookmarkOpen(true)}>
                    <Plus aria-hidden="true" />
                    New bookmark
                  </Button>
                )
              }
            />
          ) : (
            <>
              <ul className="space-y-2">
                {(visible as Bookmark[]).map((bookmark) => (
                  <EntityRow
                    key={bookmark.id}
                    title={bookmark.title || bookmark.url}
                    to={bookmark.url}
                    subtitle={bookmark.domain}
                    badge={
                      bookmark.archived_at ? (
                        <Badge variant="secondary" className="gap-1">
                          <Archive aria-hidden="true" className="size-3" />
                          Archived
                        </Badge>
                      ) : undefined
                    }
                    updatedAt={bookmark.created_at}
                    deleteLabel={`Delete ${bookmark.title || bookmark.url}`}
                    onDelete={() =>
                      setPendingDelete({
                        kind: 'bookmark',
                        id: bookmark.id,
                        title: bookmark.title || bookmark.url,
                      })
                    }
                  />
                ))}
              </ul>
              <Pager
                page={view.page}
                total={total}
                onPage={(next) => apply({ page: next }, false)}
              />
            </>
          )}
        </TabsContent>

        <TabsContent value="graph">
          <div className="space-y-3">
            <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-card p-3">
              <div className="app-form-field w-48">
                <Label htmlFor="graph-type">Entity type</Label>
                <Select
                  id="graph-type"
                  value={view.graphType ?? ''}
                  onChange={(event) =>
                    apply({
                      graphType: (event.target.value || undefined) as
                        | KnowledgeEntityType
                        | undefined,
                    })
                  }
                >
                  <option value="">Everything</option>
                  {KNOWLEDGE_ENTITY_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {KNOWLEDGE_ENTITY_META[type].label}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="app-form-field w-40">
                <Label htmlFor="graph-limit">Node cap</Label>
                <Select
                  id="graph-limit"
                  value={String(view.graphLimit)}
                  onChange={(event) => apply({ graphLimit: Number(event.target.value) })}
                >
                  {[50, 100, 200, 500].map((limit) => (
                    <option key={limit} value={limit}>
                      {limit} nodes
                    </option>
                  ))}
                </Select>
              </div>
              <p className="flex-1 text-xs leading-relaxed text-muted-foreground">
                The layout is a hand-rolled force simulation whose repulsion is O(n²). Past a couple
                of hundred nodes the picture stops carrying information, so the view caps what it
                draws and says so when the graph it received was capped too.
              </p>
            </div>
            <GraphView
              limit={view.graphLimit}
              entityType={view.graphType}
              onSelectNode={(node) => {
                if (node.type === 'note') navigate(`/knowledge/notes/${node.id}`)
                if (node.type === 'concept') navigate(`/knowledge/concepts/${node.id}`)
              }}
            />
          </div>
        </TabsContent>
      </Tabs>

      {view.tab !== 'graph' && recent.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium text-foreground">Recently updated</h2>
          <ul className="space-y-2">
            {recent.map((note) => (
              <li key={note.id}>
                <NoteCard note={note} compact onOpen={(value) => openNote(value.id)} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <Dialog open={composerOpen} onOpenChange={setComposerOpen}>
        <DialogContent className="max-w-5xl">
          <DialogHeader>
            <DialogTitle>New note</DialogTitle>
            <DialogDescription>
              Written in Markdown and saved as you type. A note starts as a draft; publishing it is
              a separate, deliberate act.
            </DialogDescription>
          </DialogHeader>
          <NoteEditor
            onSaved={(saved) => {
              setComposerOpen(false)
              navigate(`/knowledge/notes/${saved.id}`)
            }}
          />
        </DialogContent>
      </Dialog>

      <ConceptFormDialog
        open={conceptOpen}
        onOpenChange={setConceptOpen}
        onSaved={(saved) => navigate(`/knowledge/concepts/${saved.id}`)}
      />

      <ResourceDialog
        open={resourceOpen}
        onOpenChange={setResourceOpen}
        onSaved={() => apply({})}
      />

      <BookmarkFormDialog open={bookmarkOpen} onOpenChange={setBookmarkOpen} onSaved={() => apply({})} />

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null)
        }}
        title="Delete this?"
        description={
          pendingDelete
            ? `"${pendingDelete.title}" and every link pointing at it are removed. Archiving is the reversible answer for a bookmark.`
            : ''
        }
        confirmLabel="Delete"
        destructive
        pending={deleteBookmark.isPending || deleteResource.isPending || deleteConcept.isPending}
        onConfirm={confirmDelete}
      />
    </div>
  )
}