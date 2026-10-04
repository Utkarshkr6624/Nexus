/**
 * Thin typed wrappers over the Phase 5 knowledge endpoints.
 *
 * No React here — every function is a promise-returning call the hooks in
 * `features/knowledge/hooks.ts` wrap in a `queryFn`/`mutationFn`.
 *
 * Three request shapes are dictated by the router rather than chosen here:
 *
 * - `GET /knowledge/links` takes its endpoints as **query parameters** and
 *   requires exactly one complete pair, so outbound and inbound are two
 *   functions over one route rather than one function over nullable fields.
 * - Lifecycle is `POST /notes/{id}/{publish|archive|restore}`; no `*Update`
 *   payload carries `status`, so there is no update function that could set one.
 * - `GET /knowledge/search` answers `?q=` and groups its results by entity type.
 */
import { apiClient, queryFrom } from '@/lib/api-client'
import type { Paginated } from '@/types/pagination'
import type {
  BacklinkParams,
  Bookmark,
  BookmarkCreatePayload,
  BookmarkListParams,
  BookmarkUpdatePayload,
  Concept,
  ConceptCreatePayload,
  ConceptListParams,
  ConceptUpdatePayload,
  GraphParams,
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
  SearchParams,
  UUIDString,
} from '@/types/knowledge'

export const KNOWLEDGE_ENDPOINTS = {
  notes: '/knowledge/notes',
  note: (id: UUIDString) => `/knowledge/notes/${id}`,
  notePublish: (id: UUIDString) => `/knowledge/notes/${id}/publish`,
  noteArchive: (id: UUIDString) => `/knowledge/notes/${id}/archive`,
  noteRestore: (id: UUIDString) => `/knowledge/notes/${id}/restore`,
  noteRevisions: (id: UUIDString) => `/knowledge/notes/${id}/revisions`,
  noteRevision: (noteId: UUIDString, revisionId: UUIDString) =>
    `/knowledge/notes/${noteId}/revisions/${revisionId}`,
  noteRestoreRevision: (noteId: UUIDString, revisionId: UUIDString) =>
    `/knowledge/notes/${noteId}/restore-revision/${revisionId}`,
  concepts: '/knowledge/concepts',
  concept: (id: UUIDString) => `/knowledge/concepts/${id}`,
  resources: '/knowledge/resources',
  resource: (id: UUIDString) => `/knowledge/resources/${id}`,
  bookmarks: '/knowledge/bookmarks',
  bookmark: (id: UUIDString) => `/knowledge/bookmarks/${id}`,
  bookmarkArchive: (id: UUIDString) => `/knowledge/bookmarks/${id}/archive`,
  categories: '/knowledge/categories',
  category: (id: UUIDString) => `/knowledge/categories/${id}`,
  documents: '/knowledge/documents',
  document: (id: UUIDString) => `/knowledge/documents/${id}`,
  links: '/knowledge/links',
  link: (id: UUIDString) => `/knowledge/links/${id}`,
  graph: '/knowledge/graph',
  search: '/knowledge/search',
} as const

/* ---------------------------------------------------------------------- notes */

export function fetchNotes(params: NoteListParams = {}, signal?: AbortSignal): Promise<Paginated<Note>> {
  return apiClient.get<Paginated<Note>>(KNOWLEDGE_ENDPOINTS.notes, {
    query: queryFrom(params),
    signal,
  })
}

export function createNote(payload: NoteCreatePayload): Promise<Note> {
  return apiClient.post<Note>(KNOWLEDGE_ENDPOINTS.notes, payload)
}

export function fetchNote(id: UUIDString, signal?: AbortSignal): Promise<Note> {
  return apiClient.get<Note>(KNOWLEDGE_ENDPOINTS.note(id), { signal })
}

export function updateNote(id: UUIDString, payload: NoteUpdatePayload): Promise<Note> {
  return apiClient.patch<Note>(KNOWLEDGE_ENDPOINTS.note(id), payload)
}

export function deleteNote(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(KNOWLEDGE_ENDPOINTS.note(id))
}

export function publishNote(id: UUIDString): Promise<Note> {
  return apiClient.post<Note>(KNOWLEDGE_ENDPOINTS.notePublish(id))
}

export function archiveNote(id: UUIDString): Promise<Note> {
  return apiClient.post<Note>(KNOWLEDGE_ENDPOINTS.noteArchive(id))
}

export function restoreNote(id: UUIDString): Promise<Note> {
  return apiClient.post<Note>(KNOWLEDGE_ENDPOINTS.noteRestore(id))
}

export function listRevisions(
  id: UUIDString,
  params: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<Paginated<NoteRevision>> {
  return apiClient.get<Paginated<NoteRevision>>(KNOWLEDGE_ENDPOINTS.noteRevisions(id), {
    query: queryFrom(params),
    signal,
  })
}

/**
 * Writes a new revision of the current state first, so a restore is itself
 * undoable. The note's `status` is deliberately not part of it.
 */
export function restoreRevision(noteId: UUIDString, revisionId: UUIDString): Promise<Note> {
  return apiClient.post<Note>(KNOWLEDGE_ENDPOINTS.noteRestoreRevision(noteId, revisionId))
}

/* ------------------------------------------------------------------- concepts */

export function fetchConcepts(
  params: ConceptListParams = {},
  signal?: AbortSignal,
): Promise<Paginated<Concept>> {
  return apiClient.get<Paginated<Concept>>(KNOWLEDGE_ENDPOINTS.concepts, {
    query: queryFrom(params),
    signal,
  })
}

export function createConcept(payload: ConceptCreatePayload): Promise<Concept> {
  return apiClient.post<Concept>(KNOWLEDGE_ENDPOINTS.concepts, payload)
}

export function updateConcept(id: UUIDString, payload: ConceptUpdatePayload): Promise<Concept> {
  return apiClient.patch<Concept>(KNOWLEDGE_ENDPOINTS.concept(id), payload)
}

export function deleteConcept(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(KNOWLEDGE_ENDPOINTS.concept(id))
}

/* ------------------------------------------------------------------ resources */

export function fetchResources(
  params: ResourceListParams = {},
  signal?: AbortSignal,
): Promise<Paginated<Resource>> {
  return apiClient.get<Paginated<Resource>>(KNOWLEDGE_ENDPOINTS.resources, {
    query: queryFrom(params),
    signal,
  })
}

export function createResource(payload: ResourceCreatePayload): Promise<Resource> {
  return apiClient.post<Resource>(KNOWLEDGE_ENDPOINTS.resources, payload)
}

export function deleteResource(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(KNOWLEDGE_ENDPOINTS.resource(id))
}

/* ------------------------------------------------------------------- bookmarks */

export function fetchBookmarks(
  params: BookmarkListParams = {},
  signal?: AbortSignal,
): Promise<Paginated<Bookmark>> {
  return apiClient.get<Paginated<Bookmark>>(KNOWLEDGE_ENDPOINTS.bookmarks, {
    query: queryFrom(params),
    signal,
  })
}

export function createBookmark(payload: BookmarkCreatePayload): Promise<Bookmark> {
  return apiClient.post<Bookmark>(KNOWLEDGE_ENDPOINTS.bookmarks, payload)
}

export function updateBookmark(id: UUIDString, payload: BookmarkUpdatePayload): Promise<Bookmark> {
  return apiClient.patch<Bookmark>(KNOWLEDGE_ENDPOINTS.bookmark(id), payload)
}

export function deleteBookmark(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(KNOWLEDGE_ENDPOINTS.bookmark(id))
}

/* ------------------------------------------------------------------ categories */

/* ------------------------------------------------------------------- documents */

/* -------------------------------------------------------- links, graph, search */

/** Outbound edges. The router rejects a request that does not name both halves. */
export function fetchOutboundLinks(
  params: OutboundLinkParams,
  signal?: AbortSignal,
): Promise<Paginated<KnowledgeLink>> {
  return apiClient.get<Paginated<KnowledgeLink>>(KNOWLEDGE_ENDPOINTS.links, {
    query: queryFrom(params),
    signal,
  })
}

/**
 * Edges pointing **at** a node — the backlinks view.
 *
 * The same route read in the other column order. The route answers
 * `Page[KnowledgeLinkRead]` in this phase rather than the named
 * `BacklinksResponse`, so the page shape is what comes back; see
 * `BacklinksResponse` in `types/knowledge.ts` for the difference.
 */
export function fetchBacklinks(
  params: BacklinkParams,
  signal?: AbortSignal,
): Promise<Paginated<KnowledgeLink>> {
  return apiClient.get<Paginated<KnowledgeLink>>(KNOWLEDGE_ENDPOINTS.links, {
    query: queryFrom(params),
    signal,
  })
}

export function createLink(payload: KnowledgeLinkCreatePayload): Promise<KnowledgeLink> {
  return apiClient.post<KnowledgeLink>(KNOWLEDGE_ENDPOINTS.links, payload)
}

/** Removes the edge only — both endpoints survive, by design. */
export function deleteLink(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(KNOWLEDGE_ENDPOINTS.link(id))
}

/** `limit` above 500 is a 422 rather than a truncation; `truncated` says so too. */
export function fetchGraph(params: GraphParams = {}, signal?: AbortSignal): Promise<KnowledgeGraph> {
  return apiClient.get<KnowledgeGraph>(KNOWLEDGE_ENDPOINTS.graph, {
    query: queryFrom(params),
    signal,
  })
}

/** Grouped, not merged: `limit` bounds each group rather than the total. */
export function searchKnowledge(
  params: SearchParams,
  signal?: AbortSignal,
): Promise<KnowledgeSearchResult> {
  return apiClient.get<KnowledgeSearchResult>(KNOWLEDGE_ENDPOINTS.search, {
    query: queryFrom(params),
    signal,
  })
}