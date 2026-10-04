/**
 * TanStack Query bindings for the Phase 5 knowledge surface.
 *
 * **`knowledgeKeys` is the single owner of the query-key shape.** `notes()`,
 * `concepts()`, `graph()` and the rest called with no argument yield the
 * *prefix* for that family — exactly what an invalidation needs to reach every
 * list, detail and search query at once.
 *
 * **Every mutation invalidates the whole `['knowledge']` tree.** Writing a note
 * changes its own row, the counts on the dashboard, its position in the graph
 * and possibly the result of a search; picking keys per mutation is how one of
 * those four ships stale. The tree is small and the invalidation is cheap, so
 * the aggregate is the correct trade — the same argument `workKeys` makes.
 *
 * **Retry policy is inherited.** `app/query-client.ts` refuses to retry a 4xx,
 * so a 404 — another account's id, or one that never existed — surfaces as
 * not-found on the first response rather than being asked for again.
 *
 * **List keys are stabilised.** Params are projected onto a fixed-length key
 * part with the unset ones normalised to `null`, so a params object re-created
 * on every render hashes to the same key instead of thrashing the cache.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'

import {
  archiveNote,
  createBookmark,
  createConcept,
  createLink,
  createNote,
  createResource,
  deleteBookmark,
  deleteConcept,
  deleteLink,
  deleteNote,
  deleteResource,
  fetchBacklinks,
  fetchBookmarks,
  fetchConcepts,
  fetchGraph,
  fetchNote,
  fetchNotes,
  fetchOutboundLinks,
  fetchResources,
  listRevisions,
  publishNote,
  restoreNote,
  restoreRevision,
  searchKnowledge,
  updateBookmark,
  updateConcept,
  updateNote,
} from '@/services/knowledge'
import type {
  BacklinkParams,
  Bookmark,
  BookmarkCreatePayload,
  BookmarkListParams,
  BookmarkUpdatePayload,
  CategoryListParams,
  Concept,
  ConceptCreatePayload,
  ConceptListParams,
  ConceptUpdatePayload,
  DocumentListParams,
  GraphParams,
  KnowledgeEntityType,
  KnowledgeGraph,
  KnowledgeLink,
  KnowledgeLinkCreatePayload,
  KnowledgeSearchResult,
  Note,
  NoteCreatePayload,
  NoteListParams,
  NoteRevision,
  NoteUpdatePayload,
  OutboundLinkParams,
  Resource,
  ResourceCreatePayload,
  ResourceListParams,
  UUIDString,
} from '@/types/knowledge'
import type { Paginated } from '@/types/pagination'

type Enabled = { enabled?: boolean }

function noteKeyPart(params: NoteListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.status ?? null,
    params.search ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

function conceptKeyPart(params: ConceptListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.search ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

function resourceKeyPart(params: ResourceListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.search ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

function bookmarkKeyPart(params: BookmarkListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.search ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

function documentKeyPart(params: DocumentListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.search ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

function categoryKeyPart(params: CategoryListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.parent_id ?? null,
    params.sort ?? null,
    params.order ?? null,
  ]
}

/** Stable key factory. Every key lives under the `['knowledge']` root. */
export const knowledgeKeys = {
  all: () => ['knowledge'] as const,
  notes: (params?: NoteListParams) =>
    params
      ? (['knowledge', 'notes', 'list', ...noteKeyPart(params)] as readonly unknown[])
      : (['knowledge', 'notes'] as readonly unknown[]),
  note: (id: UUIDString | null | undefined) => ['knowledge', 'note', id ?? null] as const,
  revisions: (id: UUIDString | null | undefined) =>
    ['knowledge', 'note', id ?? null, 'revisions'] as const,
  concepts: (params?: ConceptListParams) =>
    params
      ? (['knowledge', 'concepts', 'list', ...conceptKeyPart(params)] as readonly unknown[])
      : (['knowledge', 'concepts'] as readonly unknown[]),
  resources: (params?: ResourceListParams) =>
    params
      ? (['knowledge', 'resources', 'list', ...resourceKeyPart(params)] as readonly unknown[])
      : (['knowledge', 'resources'] as readonly unknown[]),
  bookmarks: (params?: BookmarkListParams) =>
    params
      ? (['knowledge', 'bookmarks', 'list', ...bookmarkKeyPart(params)] as readonly unknown[])
      : (['knowledge', 'bookmarks'] as readonly unknown[]),
  categories: (params?: CategoryListParams) =>
    params
      ? (['knowledge', 'categories', 'list', ...categoryKeyPart(params)] as readonly unknown[])
      : (['knowledge', 'categories'] as readonly unknown[]),
  documents: (params?: DocumentListParams) =>
    params
      ? (['knowledge', 'documents', 'list', ...documentKeyPart(params)] as readonly unknown[])
      : (['knowledge', 'documents'] as readonly unknown[]),
  /** Outbound edges of one node. Takes the same params the request carries. */
  links: (params?: OutboundLinkParams) =>
    params
      ? ([
          'knowledge',
          'links',
          'out',
          params.source_type,
          params.source_id,
          params.link_type ?? null,
        ] as const)
      : (['knowledge', 'links'] as readonly unknown[]),
  /** Edges arriving at one node. */
  backlinks: (params?: BacklinkParams) =>
    params
      ? ([
          'knowledge',
          'links',
          'in',
          params.target_type,
          params.target_id,
          params.link_type ?? null,
        ] as const)
      : (['knowledge', 'links', 'in'] as readonly unknown[]),
  graph: (params?: GraphParams) =>
    params
      ? (['knowledge', 'graph', params.entity_type ?? null, params.limit ?? null] as const)
      : (['knowledge', 'graph'] as readonly unknown[]),
  search: (query?: string, type?: string, limit?: number) =>
    ['knowledge', 'search', query ?? '', type ?? null, limit ?? null] as readonly unknown[],
}

/* ------------------------------------------------------------------ queries */

export function useNotes(
  params: NoteListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<Note>> {
  return useQuery({
    queryKey: knowledgeKeys.notes(params),
    queryFn: ({ signal }) => fetchNotes(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useNote(id: UUIDString | null | undefined): UseQueryResult<Note> {
  return useQuery({
    queryKey: knowledgeKeys.note(id),
    queryFn: ({ signal }) => fetchNote(id as UUIDString, signal),
    enabled: Boolean(id),
  })
}

export function useNoteRevisions(
  id: UUIDString | null | undefined,
  params: { limit?: number; offset?: number } = {},
): UseQueryResult<Paginated<NoteRevision>> {
  return useQuery({
    queryKey: [...knowledgeKeys.revisions(id), params.limit ?? null, params.offset ?? null],
    queryFn: ({ signal }) => listRevisions(id as UUIDString, params, signal),
    enabled: Boolean(id),
  })
}

export function useConcepts(
  params: ConceptListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<Concept>> {
  return useQuery({
    queryKey: knowledgeKeys.concepts(params),
    queryFn: ({ signal }) => fetchConcepts(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useResources(
  params: ResourceListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<Resource>> {
  return useQuery({
    queryKey: knowledgeKeys.resources(params),
    queryFn: ({ signal }) => fetchResources(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

export function useBookmarks(
  params: BookmarkListParams = {},
  options: Enabled = {},
): UseQueryResult<Paginated<Bookmark>> {
  return useQuery({
    queryKey: knowledgeKeys.bookmarks(params),
    queryFn: ({ signal }) => fetchBookmarks(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/** Edges leaving a node. Disabled until both halves of the pair are known. */
export function useOutboundLinks(
  params: Partial<OutboundLinkParams> | undefined,
): UseQueryResult<Paginated<KnowledgeLink>> {
  const complete: OutboundLinkParams | null =
    params?.source_type && params?.source_id
      ? {
          source_type: params.source_type,
          source_id: params.source_id,
          link_type: params.link_type,
        }
      : null

  return useQuery({
    queryKey: knowledgeKeys.links(complete ?? undefined),
    queryFn: ({ signal }) => fetchOutboundLinks(complete as OutboundLinkParams, signal),
    enabled: complete !== null,
    placeholderData: (previous) => previous,
  })
}

/**
 * Edges arriving at a node — the backlinks panel.
 *
 * Accepts either the params object or the `(type, id)` pair a panel has on
 * hand, because every caller holds exactly those two values and building an
 * object for them at each call site is noise.
 */
export function useBacklinks(
  paramsOrType?: Partial<BacklinkParams> | KnowledgeEntityType,
  entityId?: UUIDString,
): UseQueryResult<Paginated<KnowledgeLink>> {
  const params: Partial<BacklinkParams> | undefined =
    typeof paramsOrType === 'string' ? { target_type: paramsOrType, target_id: entityId } : paramsOrType
  return useBacklinksQuery(params)
}

function useBacklinksQuery(
  params: Partial<BacklinkParams> | undefined,
): UseQueryResult<Paginated<KnowledgeLink>> {
  const complete: BacklinkParams | null =
    params?.target_type && params?.target_id
      ? {
          target_type: params.target_type,
          target_id: params.target_id,
          link_type: params.link_type,
        }
      : null

  return useQuery({
    queryKey: knowledgeKeys.backlinks(complete ?? undefined),
    queryFn: ({ signal }) => fetchBacklinks(complete as BacklinkParams, signal),
    enabled: complete !== null,
    placeholderData: (previous) => previous,
  })
}

export function useKnowledgeGraph(
  params: GraphParams = {},
  options: Enabled = {},
): UseQueryResult<KnowledgeGraph> {
  return useQuery({
    queryKey: knowledgeKeys.graph(params),
    queryFn: ({ signal }) => fetchGraph(params, signal),
    enabled: options.enabled,
  })
}

/** Grouped by entity type server-side, so there is nothing to merge here. */
export function useKnowledgeSearch(
  query: string,
  options: { type?: string; limit?: number } = {},
): UseQueryResult<KnowledgeSearchResult> {
  const term = query.trim()
  return useQuery({
    queryKey: knowledgeKeys.search(term, options.type, options.limit),
    queryFn: ({ signal }) =>
      searchKnowledge({ q: term, type: options.type as never, limit: options.limit }, signal),
    enabled: term.length > 0,
  })
}

/* ---------------------------------------------------------------- mutations */

/**
 * Every mutation invalidates the same aggregate prefix, so no write can ship
 * having left the dashboard counts, the graph or a search result stale.
 */
function useInvalidateKnowledge() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: knowledgeKeys.all() })
  }
}

export function useCreateNote(): UseMutationResult<Note, Error, NoteCreatePayload> {
  return useMutation({ mutationFn: createNote, onSuccess: useInvalidateKnowledge() })
}

export function useUpdateNote(): UseMutationResult<
  Note,
  Error,
  { id: UUIDString; payload: NoteUpdatePayload }
> {
  return useMutation({
    mutationFn: ({ id, payload }) => updateNote(id, payload),
    onSuccess: useInvalidateKnowledge(),
  })
}

export function useDeleteNote(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteNote, onSuccess: useInvalidateKnowledge() })
}

/** `publish`, `archive` and `restore` are three routes with one shape. */
export function useNoteTransition(): UseMutationResult<
  Note,
  Error,
  { id: UUIDString; transition: 'publish' | 'archive' | 'restore' }
> {
  return useMutation({
    mutationFn: ({ id, transition }) => {
      if (transition === 'publish') return publishNote(id)
      if (transition === 'archive') return archiveNote(id)
      return restoreNote(id)
    },
    onSuccess: useInvalidateKnowledge(),
  })
}

/** Restoring is itself undoable — the current text is written as a revision first. */
export function useRestoreRevision(): UseMutationResult<
  Note,
  Error,
  { id: UUIDString; revisionId: UUIDString }
> {
  return useMutation({
    mutationFn: ({ id, revisionId }) => restoreRevision(id, revisionId),
    onSuccess: useInvalidateKnowledge(),
  })
}

export function useCreateConcept(): UseMutationResult<
  Concept,
  Error,
  ConceptCreatePayload
> {
  return useMutation({ mutationFn: createConcept, onSuccess: useInvalidateKnowledge() })
}

export function useUpdateConcept(): UseMutationResult<
  Concept,
  Error,
  { id: UUIDString; payload: ConceptUpdatePayload }
> {
  return useMutation({
    mutationFn: ({ id, payload }) => updateConcept(id, payload),
    onSuccess: useInvalidateKnowledge(),
  })
}

export function useDeleteConcept(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteConcept, onSuccess: useInvalidateKnowledge() })
}

export function useCreateResource(): UseMutationResult<Resource, Error, ResourceCreatePayload> {
  return useMutation({ mutationFn: createResource, onSuccess: useInvalidateKnowledge() })
}

export function useDeleteResource(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteResource, onSuccess: useInvalidateKnowledge() })
}

export function useCreateBookmark(): UseMutationResult<
  Bookmark,
  Error,
  BookmarkCreatePayload
> {
  return useMutation({ mutationFn: createBookmark, onSuccess: useInvalidateKnowledge() })
}

export function useUpdateBookmark(): UseMutationResult<
  Bookmark,
  Error,
  { id: UUIDString; payload: BookmarkUpdatePayload }
> {
  return useMutation({
    mutationFn: ({ id, payload }) => updateBookmark(id, payload),
    onSuccess: useInvalidateKnowledge(),
  })
}

export function useDeleteBookmark(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteBookmark, onSuccess: useInvalidateKnowledge() })
}

export function useCreateLink(): UseMutationResult<KnowledgeLink, Error, KnowledgeLinkCreatePayload> {
  return useMutation({ mutationFn: createLink, onSuccess: useInvalidateKnowledge() })
}

export function useDeleteLink(): UseMutationResult<void, Error, UUIDString> {
  return useMutation({ mutationFn: deleteLink, onSuccess: useInvalidateKnowledge() })
}

