/**
 * Wire types and presentation vocabulary for the Phase 5 knowledge base.
 *
 * Mirrors `backend/app/schemas/knowledge.py` and the three enums it draws on.
 * Three shapes here are dictated by the backend rather than chosen by this file:
 *
 * - `tags` are **ids**. `NoteRead.tag_ids` is a list of `UUID`s and nothing else;
 *   the names come from `GET /tags` and are joined client-side, because the
 *   knowledge endpoints deliberately do not widen into the tag tables.
 * - `search` answers with **one list per entity type**, not one merged ranked
 *   list. Merging would need a relevance model over heterogeneous columns, so
 *   the backend sends sections and the client renders sections.
 * - `graph` echoes its own `limit` and a `truncated` flag, because a caller who
 *   received exactly `limit` nodes otherwise cannot tell a capped graph from a
 *   complete one.
 */
import {
  Archive,
  BookMarked,
  CircleDashed,
  Eye,
  FileText,
  FileType,
  Globe,
  GraduationCap,
  Link2,
  Newspaper,
  Package,
  Play,
  Quote,
  Video,
} from 'lucide-react'

import type { ISODateTimeString, UUIDString } from './api'
import type { Paginated, PaginationParams } from './pagination'
import type { SortOrder, StatusMeta } from './work'

export type { ISODateTimeString, Paginated, PaginationParams, UUIDString }

export type NoteStatus = 'draft' | 'published' | 'archived'

/** The eight kinds of external thing a resource can point at. */
export type ResourceType =
  | 'article'
  | 'video'
  | 'course'
  | 'documentation'
  | 'repository'
  | 'paper'
  | 'website'
  | 'other'

/**
 * Which table an edge endpoint names. `knowledge_links` is polymorphic, so this
 * column is what decides where `source_id` points — which is also why the
 * backend resolves both endpoints through an owner-scoped lookup.
 */
export type KnowledgeEntityType = 'note' | 'concept' | 'resource'

/**
 * What an edge means. A table the UI filters on rather than a switch statement:
 * the spec forbids hardcoding relationship types through the application, so a
 * new member is an ordinary backend addition and renders here generically.
 */
export type KnowledgeLinkType =
  | 'references'
  | 'explains'
  | 'related_to'
  | 'supports'
  | 'uses'
  | 'requires'

export const NOTE_STATUS_META: Record<NoteStatus, StatusMeta> = {
  draft: {
    label: 'Draft',
    icon: CircleDashed,
    tone: 'neutral',
    description: 'Not asserted yet. What an autosaving editor produces.',
  },
  published: {
    label: 'Published',
    icon: Eye,
    tone: 'success',
    description: 'Asserted as real. The state a link target is meant to point at.',
  },
  archived: {
    label: 'Archived',
    icon: Archive,
    tone: 'warning',
    description: 'Set aside, keeping its revisions and its edges.',
  },
}

export const RESOURCE_TYPE_META: Record<ResourceType, StatusMeta> = {
  article: { label: 'Article', icon: Newspaper, tone: 'info', description: 'Written piece.' },
  video: { label: 'Video', icon: Video, tone: 'info', description: 'Recorded talk or screencast.' },
  course: { label: 'Course', icon: GraduationCap, tone: 'info', description: 'Structured series of lessons.' },
  documentation: {
    label: 'Documentation',
    icon: FileType,
    tone: 'neutral',
    description: "Someone else's reference material.",
  },
  repository: {
    label: 'Repository',
    icon: Package,
    tone: 'neutral',
    description: 'Source tree or package.',
  },
  paper: { label: 'Paper', icon: Quote, tone: 'info', description: 'Published research.' },
  website: { label: 'Website', icon: Globe, tone: 'neutral', description: 'A site or a page on one.' },
  other: {
    label: 'Other',
    icon: FileText,
    tone: 'neutral',
    description: 'Real, and fits none of the other kinds.',
  },
}

const ENTITY_ICON_LABEL: Record<KnowledgeEntityType, { label: string; icon: typeof FileText }> = {
  note: { label: 'Note', icon: FileText },
  concept: { label: 'Concept', icon: Link2 },
  resource: { label: 'Resource', icon: BookMarked },
}

export const KNOWLEDGE_ENTITY_META: Record<KnowledgeEntityType, StatusMeta> = {
  note: {
    label: ENTITY_ICON_LABEL.note.label,
    icon: ENTITY_ICON_LABEL.note.icon,
    tone: 'info',
    description: 'A written note.',
  },
  concept: {
    label: ENTITY_ICON_LABEL.concept.label,
    icon: ENTITY_ICON_LABEL.concept.icon,
    tone: 'neutral',
    description: 'A named idea.',
  },
  resource: {
    label: ENTITY_ICON_LABEL.resource.label,
    icon: ENTITY_ICON_LABEL.resource.icon,
    tone: 'neutral',
    description: 'Something filed from outside NEXUS.',
  },
}

/** One line of prose, not a colour: each tone maps onto an existing token. */
export const LINK_TYPE_META: Record<KnowledgeLinkType, StatusMeta> = {
  references: {
    label: 'References',
    icon: Quote,
    tone: 'info',
    description: 'This points at that.',
  },
  explains: {
    label: 'Explains',
    icon: GraduationCap,
    tone: 'success',
    description: 'This works through that.',
  },
  related_to: {
    label: 'Related to',
    icon: Link2,
    tone: 'neutral',
    description: 'Two things about the same subject.',
  },
  supports: {
    label: 'Supports',
    icon: BookMarked,
    tone: 'success',
    description: 'Evidence for that.',
  },
  uses: {
    label: 'Uses',
    icon: Package,
    tone: 'info',
    description: 'This makes use of that.',
  },
  requires: {
    label: 'Requires',
    icon: Play,
    tone: 'warning',
    description: 'This depends on that.',
  },
}

export const NOTE_STATUSES = Object.keys(NOTE_STATUS_META) as NoteStatus[]
export const RESOURCE_TYPES = Object.keys(RESOURCE_TYPE_META) as ResourceType[]
export const KNOWLEDGE_ENTITY_TYPES = Object.keys(KNOWLEDGE_ENTITY_META) as KnowledgeEntityType[]
export const KNOWLEDGE_LINK_TYPES = Object.keys(LINK_TYPE_META) as KnowledgeLinkType[]

/* -------------------------------------------------------------------------- */
/* Wire models                                                                 */
/* -------------------------------------------------------------------------- */

export interface Note {
  id: UUIDString
  owner_id: UUIDString
  title: string
  /** Markdown source. Stored as Markdown, which is what makes export trivial. */
  content: string
  summary: string | null
  status: NoteStatus
  /** Provenance — a note may name the document it was written from. */
  document_id: UUIDString | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
  tag_ids: UUIDString[]
  revision_count: number
  /** Derived backend-side from `status`; never recomputed here. */
  is_archived: boolean
}

/**
 * One point-in-time copy of a note's text. Carries no `status` on purpose:
 * restoring one assigns title/content/summary and nothing else, so an edit made
 * after publishing cannot silently un-publish a note.
 */
export interface NoteRevision {
  id: UUIDString
  note_id: UUIDString
  owner_id: UUIDString
  title: string
  content: string
  summary: string | null
  created_at: ISODateTimeString
}

export interface Concept {
  id: UUIDString
  owner_id: UUIDString
  name: string
  description: string | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
  tag_ids: UUIDString[]
}

export interface Resource {
  id: UUIDString
  owner_id: UUIDString
  title: string
  description: string | null
  url: string | null
  resource_type: ResourceType
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

export interface Bookmark {
  id: UUIDString
  owner_id: UUIDString
  url: string
  title: string | null
  description: string | null
  /** Always the server's derivation of `url`, never a client-supplied label. */
  domain: string | null
  archived_at: ISODateTimeString | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/** Metadata only — this phase uploads and parses nothing. */
export interface Document {
  id: UUIDString
  owner_id: UUIDString
  filename: string
  title: string | null
  description: string | null
  document_type: string | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

export interface KnowledgeCategory {
  id: UUIDString
  owner_id: UUIDString
  name: string
  parent_id: UUIDString | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

export interface KnowledgeLink {
  id: UUIDString
  owner_id: UUIDString
  source_type: KnowledgeEntityType
  source_id: UUIDString
  target_type: KnowledgeEntityType
  target_id: UUIDString
  link_type: KnowledgeLinkType
  created_at: ISODateTimeString
}

export interface KnowledgeGraphNode {
  id: UUIDString
  type: KnowledgeEntityType
  label: string
}

export interface KnowledgeGraphEdge {
  source: UUIDString
  target: UUIDString
  source_type: KnowledgeEntityType
  target_type: KnowledgeEntityType
  type: KnowledgeLinkType
}

export interface KnowledgeGraph {
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
  limit: number
  /** Exactly `limit` nodes came back and more exist. */
  truncated: boolean
}

export interface KnowledgeSearchResult {
  query: string
  /** Bounds **each** group, not the total. */
  limit: number
  notes: Note[]
  concepts: Concept[]
  resources: Resource[]
  bookmarks: Bookmark[]
}

/**
 * The named backlink envelope from `backend/app/schemas/knowledge.py`.
 *
 * **`GET /knowledge/links?target_type=&target_id=` currently answers
 * `Page[KnowledgeLinkRead]` instead**, so `fetchBacklinks` returns that page.
 * The shape is kept here so a peer surface can accept either, and because the
 * backend will only ever grow away from the ad-hoc page.
 */
export interface BacklinksResponse {
  entity_type: KnowledgeEntityType
  entity_id: UUIDString
  backlinks: KnowledgeLink[]
  total: number
}

export type NotePage = Paginated<Note>
export type NoteRevisionPage = Paginated<NoteRevision>
export type ConceptPage = Paginated<Concept>
export type ResourcePage = Paginated<Resource>
export type BookmarkPage = Paginated<Bookmark>
export type DocumentPage = Paginated<Document>
export type CategoryPage = Paginated<KnowledgeCategory>
export type KnowledgeLinkPage = Paginated<KnowledgeLink>

export interface NoteListParams extends PaginationParams {
  status?: NoteStatus
  search?: string
  sort?: string
  order?: SortOrder
}

export interface ConceptListParams extends PaginationParams {
  search?: string
  sort?: string
  order?: SortOrder
}

export interface ResourceListParams extends PaginationParams {
  search?: string
  sort?: string
  order?: SortOrder
}

export interface BookmarkListParams extends PaginationParams {
  search?: string
  sort?: string
  order?: SortOrder
}

export interface DocumentListParams extends PaginationParams {
  search?: string
  sort?: string
  order?: SortOrder
}

export interface CategoryListParams extends PaginationParams {
  parent_id?: UUIDString
  sort?: string
  order?: SortOrder
}

/**
 * `GET /knowledge/links` takes **exactly one complete endpoint pair**. Naming
 * neither, half a pair or both is a 422 from the service, so these two shapes
 * are kept apart rather than merged into one nullable record.
 */
export interface OutboundLinkParams extends PaginationParams {
  source_type: KnowledgeEntityType
  source_id: UUIDString
  link_type?: KnowledgeLinkType
}

export interface BacklinkParams extends PaginationParams {
  target_type: KnowledgeEntityType
  target_id: UUIDString
  link_type?: KnowledgeLinkType
}

export interface GraphParams {
  limit?: number
  entity_type?: KnowledgeEntityType
}

export interface SearchParams {
  q: string
  type?: KnowledgeEntityType
  limit?: number
}

/* -------------------------------------------------------------------------- */
/* Sort allowlists                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Sort keys each endpoint advertises. The service resolves the name against
 * its own set and 422s anything else, so the client validates against these
 * before sending — a stale bookmark narrows the list instead of breaking it.
 */
export const NOTE_SORT_KEYS = ['updated_at', 'created_at', 'title', 'status'] as const
export const CONCEPT_SORT_KEYS = ['name', 'updated_at', 'created_at'] as const
export const RESOURCE_SORT_KEYS = ['updated_at', 'created_at', 'title', 'resource_type'] as const
export const BOOKMARK_SORT_KEYS = ['created_at', 'updated_at', 'archived_at', 'domain'] as const
export type NoteSortKey = (typeof NOTE_SORT_KEYS)[number]
export type ConceptSortKey = (typeof CONCEPT_SORT_KEYS)[number]
export type ResourceSortKey = (typeof RESOURCE_SORT_KEYS)[number]
export type BookmarkSortKey = (typeof BOOKMARK_SORT_KEYS)[number]

export const SORT_LABELS: Record<string, string> = {
  updated_at: 'Last updated',
  created_at: 'Created',
  title: 'Title',
  status: 'Status',
  name: 'Name',
  resource_type: 'Type',
  archived_at: 'Archived',
  domain: 'Domain',
  filename: 'Filename',
}

/** `limit` above 100 is a 422 on every list, and a 422 on the graph above 500. */
export const MAX_PAGE_SIZE = 100
/* -------------------------------------------------------------------------- */
/* Request payloads                                                            */
/* -------------------------------------------------------------------------- */

/**
 * `NoteUpdate` is `extra="forbid"` and carries neither `status` nor
 * `document_id`: the lifecycle has endpoints and provenance is not an edit.
 */
export interface NoteCreatePayload {
  title: string
  content?: string
  summary?: string | null
  document_id?: UUIDString | null
  tag_ids?: UUIDString[]
}

export type NoteUpdatePayload = Partial<NoteCreatePayload>

export interface ConceptCreatePayload {
  name: string
  description?: string | null
  tag_ids?: UUIDString[]
}

export type ConceptUpdatePayload = Partial<ConceptCreatePayload>

export interface ResourceCreatePayload {
  title: string
  description?: string | null
  url?: string | null
  resource_type?: ResourceType
}

export type ResourceUpdatePayload = Partial<ResourceCreatePayload>

export interface BookmarkCreatePayload {
  url: string
  title?: string | null
  description?: string | null
}

export type BookmarkUpdatePayload = Partial<BookmarkCreatePayload>

export interface DocumentCreatePayload {
  filename: string
  title?: string | null
  description?: string | null
  document_type?: string | null
}

export type DocumentUpdatePayload = Partial<DocumentCreatePayload>

export interface CategoryCreatePayload {
  name: string
  parent_id?: UUIDString | null
}

export type CategoryUpdatePayload = Partial<CategoryCreatePayload>

/** A self edge is a 422; the same edge twice is a 409. */
export interface KnowledgeLinkCreatePayload {
  source_type: KnowledgeEntityType
  source_id: UUIDString
  target_type: KnowledgeEntityType
  target_id: UUIDString
  link_type?: KnowledgeLinkType
}

/* -------------------------------------------------------------------------- */
/* Formatting helpers                                                          */
/* -------------------------------------------------------------------------- */

const ABSOLUTE_DATE = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

const ABSOLUTE_DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

/**
 * `2026-01-08` → "8 Jan 2026". **Never prints `Invalid Date`** — an
 * unparseable or absent timestamp answers "Unknown", because a list row that
 * says "Invalid Date" looks like a bug in the row and not like bad data.
 */
export function formatKnowledgeDate(iso: string | null | undefined): string {
  const date = parse(iso)
  return date ? ABSOLUTE_DATE.format(date) : 'Unknown'
}

/** As {@link formatKnowledgeDate}, but with the clock time. */
export function formatKnowledgeDateTime(iso: string | null | undefined): string {
  const date = parse(iso)
  return date ? ABSOLUTE_DATE_TIME.format(date) : 'Unknown'
}

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * "just now" / "3 days ago" / "in 2 hours", and "Unknown" for anything the
 * parser cannot read. Same rule as {@link formatKnowledgeDate}: this string
 * appears next to every row, so `NaN` here is `NaN` everywhere.
 */
export function formatRelative(iso: string | null | undefined): string {
  const date = parse(iso)
  if (!date) return 'Unknown'

  const delta = date.getTime() - Date.now()
  const magnitude = Math.abs(delta)
  const suffix = (value: number, unit: string): string =>
    `${value} ${unit}${value === 1 ? '' : 's'}`

  if (magnitude < MINUTE) return 'just now'
  if (magnitude < HOUR) return suffix(Math.round(magnitude / MINUTE), 'minute') + (delta < 0 ? ' ago' : '')
  if (magnitude < DAY) return suffix(Math.round(magnitude / HOUR), 'hour') + (delta < 0 ? ' ago' : '')
  if (magnitude < 30 * DAY) return suffix(Math.round(magnitude / DAY), 'day') + (delta < 0 ? ' ago' : '')
  if (delta < 0) return suffix(Math.round(magnitude / (30 * DAY)), 'month') + ' ago'
  return ABSOLUTE_DATE.format(date)
}

/** Truncates on a word boundary so a preview never ends mid-word. */
export function summarise(text: string | null | undefined, maxLength = 180): string {
  const trimmed = (text ?? '').trim()
  if (trimmed.length <= maxLength) return trimmed
  const cut = trimmed.slice(0, maxLength)
  const lastSpace = cut.lastIndexOf(' ')
  return `${(lastSpace > maxLength * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`
}

/* -------------------------------------------------------------------------- */
/* Markdown                                                                    */
/* -------------------------------------------------------------------------- */

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * The schemes a rendered link may use. Relative links and fragments are kept
 * because a note full of them is useful; `javascript:` and `data:` are not,
 * because a link that executes when clicked is a stored script.
 */
const SAFE_LINK = /^(https?:\/\/|\.{0,2}\/|#)/i

function safeUrl(url: string): string | null {
  // Control characters and whitespace are stripped before the scheme test:
  // `java\tscript:` is treated as `javascript:` by browsers, and a check that
  // misses it is a check that lies.
  //
  // The candidate is returned unescaped because by the time a link is matched
  // the whole line has already been through `escapeHtml`; escaping here a second
  // time is what turns `?a=1&b=2` into a link pointing at the wrong address.
  const candidate = url.trim()
  const collapsed = candidate.replace(/[\p{Cc}\s]/gu, '')
  if (/^javascript:/i.test(collapsed)) return null
  return SAFE_LINK.test(collapsed) ? candidate : null
}

/**
 * Placeholder marking a lifted code span. A private-use character, not NUL:
 * it cannot occur in the source, it survives `escapeHtml` untouched, and it
 * keeps the sentinel out of the control-character class entirely.
 */
const CODE_SENTINEL = ''

function renderInline(text: string): string {
  // Code spans are lifted out first so their contents are escaped once and are
  // never re-processed as emphasis or link syntax.
  const codeSpans: string[] = []
  let working = text.replace(/`([^`]+)`/g, (_match, code: string) => {
    codeSpans.push(`<code>${escapeHtml(code)}</code>`)
    return `${CODE_SENTINEL}${codeSpans.length - 1}${CODE_SENTINEL}`
  })

  working = escapeHtml(working)

  working = working.replace(
    /\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g,
    (match, label: string, url: string) => {
      const href = safeUrl(url)
      if (!href) return match
      const external = href.startsWith('http')
      return `<a href="${href}"${
        external ? ' target="_blank" rel="noopener noreferrer nofollow"' : ''
      }>${label}</a>`
    },
  )

  working = working
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[^_\w])_([^_\n]+)_/g, '$1<em>$2</em>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')

  const restore = new RegExp(`${CODE_SENTINEL}(\\d+)${CODE_SENTINEL}`, 'g')
  return working.replace(restore, (_match, index: string) => codeSpans[Number(index)] ?? '')
}

/**
 * A second pass over generated HTML. {@link escapeHtml} already guarantees
 * every character of user text is inert, so this is defence in depth rather
 * than the primary guard: if a future inline rule ever emits an attribute
 * without escaping through, this is the pass that catches it.
 *
 * Each pattern is anchored inside a real tag (`<name …`). That matters because
 * escaped user text contains no `<` at all, so a pattern loose enough to match
 * bare text would be deleting characters from prose rather than from markup.
 */
function sanitiseHtml(html: string): string {
  return html
    .replace(/<\s*(script|style|iframe|object|embed|form)\b[\s\S]*?(?:<\s*\/\s*\1\s*>|$)/gi, '')
    .replace(/(<[a-z][\w-]*\b[^>]*?)\son\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]*)/gi, '$1')
    .replace(
      /(href|src)\s*=\s*(?:"\s*javascript:[^"]*"|'\s*javascript:[^']*'|javascript:[^\s>]*)/gi,
      '$1="#"',
    )
}

/**
 * Markdown source → sanitised HTML.
 *
 * Deliberately small and total: headings, paragraphs, fenced and inline code,
 * blockquotes, ordered/unordered lists, rules, links, emphasis and strikethrough.
 * No HTML passthrough, no images — a note is written here by the user, and a
 * renderer that accepts raw tags from the note body would make every stored
 * note an injection vector.
 */
export function renderMarkdown(markdown: string | null | undefined): string {
  if (!markdown) return ''

  const lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  const html: string[] = []

  let paragraph: string[] = []
  let listItems: string[] = []
  let listTag: 'ul' | 'ol' = 'ul'
  let quoteLines: string[] = []
  let codeLines: string[] = []
  let codeLanguage = ''
  let inCode = false

  const flushParagraph = (): void => {
    if (paragraph.length === 0) return
    html.push(`<p>${renderInline(paragraph.join('\n')).replace(/\n/g, '<br />')}</p>`)
    paragraph = []
  }
  const flushList = (): void => {
    if (listItems.length === 0) return
    const items = listItems.map((item) => {
      // A GitHub-style task item. The checkbox is rendered `disabled`, so it
      // carries no event handler and the reader cannot be made to run anything
      // by it; ticking a box is an edit to the Markdown, not a click here.
      const task = /^\[([ xX])\]\s+(.*)$/.exec(item)
      if (!task) return `<li>${renderInline(item)}</li>`
      const done = (task[1] ?? ' ').toLowerCase() === 'x'
      const className = done ? ' class="task-list-item-complete"' : ''
      return `<li${className}><input type="checkbox" disabled${done ? ' checked' : ''} /> ${renderInline(task[2] ?? '')}</li>`
    }).join('')
    html.push(`<${listTag}>${items}</${listTag}>`)
    listItems = []
  }
  const flushQuote = (): void => {
    if (quoteLines.length === 0) return
    html.push(`<blockquote>${renderInline(quoteLines.join('\n'))}</blockquote>`)
    quoteLines = []
  }
  const flushCode = (): void => {
    const className = codeLanguage ? ` class="language-${escapeHtml(codeLanguage)}"` : ''
    html.push(`<pre><code${className}>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
    codeLines = []
    codeLanguage = ''
  }
  const flushBlocks = (): void => {
    flushParagraph()
    flushList()
    flushQuote()
  }

  for (const line of lines) {
    const fence = /^```\s*([\w+-]*)\s*$/.exec(line)
    if (fence) {
      if (inCode) {
        flushCode()
        inCode = false
      } else {
        flushBlocks()
        inCode = true
        codeLanguage = fence[1] ?? ''
      }
      continue
    }
    if (inCode) {
      codeLines.push(line)
      continue
    }

    if (line.trim() === '') {
      flushBlocks()
      continue
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      flushBlocks()
      const level = (heading[1] ?? '#').length
      html.push(`<h${level}>${renderInline(heading[2] ?? '')}</h${level}>`)
      continue
    }

    if (/^(\s*[-*_]){3,}\s*$/.test(line)) {
      flushBlocks()
      html.push('<hr />')
      continue
    }

    if (/^>\s?/.test(line)) {
      flushParagraph()
      flushList()
      quoteLines.push(line.replace(/^>\s?/, ''))
      continue
    }

    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line)
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (bullet || numbered) {
      flushParagraph()
      flushQuote()
      const nextTag = bullet ? 'ul' : 'ol'
      if (listItems.length > 0 && nextTag !== listTag) flushList()
      listTag = nextTag
      listItems.push((bullet?.[1] ?? numbered?.[1] ?? '').trim())
      continue
    }

    flushList()
    flushQuote()
    paragraph.push(line)
  }

  if (inCode) flushCode()
  flushBlocks()

  return sanitiseHtml(html.join('\n'))
}