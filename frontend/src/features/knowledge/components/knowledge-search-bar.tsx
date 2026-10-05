import { useEffect, useState } from 'react'
import { Search, X } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { Spinner } from '@/components/ui/spinner'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { useKnowledgeSearch } from '@/features/knowledge/hooks'
import { toApiError } from '@/services/errors'
import { cn } from '@/lib/utils'
import { KNOWLEDGE_ENTITY_TYPES, KNOWLEDGE_ENTITY_META } from '@/types/knowledge'
import type { KnowledgeEntityType } from '@/types/knowledge'

import { EmptyKnowledge } from './empty-knowledge'

export interface KnowledgeSearchBarProps {
  /** Reported as the user types, already debounced. */
  onQueryChange?: (query: string) => void
  /** Restricts the search to one kind; the bar offers the filter itself. */
  onTypeChange?: (type: KnowledgeEntityType | undefined) => void
  type?: KnowledgeEntityType
  /** Hides the result panel for callers that render their own. */
  showResults?: boolean
  placeholder?: string
  className?: string
}

const DEBOUNCE_MS = 300

/** `MAX_SEARCH_LENGTH` upstream: a longer term is a paste, and it is a 422. */
const MAX_QUERY_LENGTH = 200

/** The three node kinds plus bookmarks, which search covers but the graph does not. */
type ResultType = KnowledgeEntityType | 'bookmark'

function ResultList({ query, type }: { query: string; type?: KnowledgeEntityType }) {
  const { data, isPending, error, refetch } = useKnowledgeSearch(query, { type })
  /**
   * `KnowledgeSearchResult` answers with four lists while
   * `KNOWLEDGE_ENTITY_TYPES` names three kinds — a bookmark is not a node, so it
   * has no kind to group under. Filing it under "Resources" rendered a saved link
   * as though it were a filed resource, so bookmarks get their own heading
   * instead of borrowing one.
   */
  const results = data
    ? [
        ...data.notes.map((note) => ({
          key: note.id,
          type: 'note' as const,
          label: note.title,
          detail: note.summary ?? note.content.slice(0, 120),
        })),
        ...data.concepts.map((concept) => ({
          key: concept.id,
          type: 'concept' as const,
          label: concept.name,
          detail: concept.description ?? '',
        })),
        ...data.resources.map((resource) => ({
          key: resource.id,
          type: 'resource' as const,
          label: resource.title,
          detail: resource.url ?? '',
        })),
        ...data.bookmarks.map((bookmark) => ({
          key: bookmark.id,
          type: 'bookmark' as const,
          label: bookmark.title ?? bookmark.url,
          detail: bookmark.domain ?? '',
        })),
      ].filter((row) => type === undefined || row.type === type)
    : []

  if (isPending) {
    return (
      <div className="flex items-center justify-center py-6">
        <Spinner size="sm" label="Searching" />
      </div>
    )
  }

  if (error) {
    const apiError = toApiError(error)
    return (
      <ErrorState
        compact
        error={apiError}
        onRetry={() => void refetch()}
        title="The search could not run"
      />
    )
  }

  if (results.length === 0) {
    return <EmptyKnowledge kind="search" compact />
  }

  // Grouped by kind, because the response is: a relevance ranking across four
  // heterogeneous tables would answer a different question from the one asked.
  const headings: ReadonlyArray<readonly [ResultType, string]> = [
    ...KNOWLEDGE_ENTITY_TYPES.map(
      (kind) => [kind, KNOWLEDGE_ENTITY_META[kind].label] as const,
    ),
    ['bookmark', 'Bookmarks'],
  ]
  const groups = headings
    .map(([kind, heading]) => ({
      kind,
      heading,
      rows: results.filter((row) => row.type === kind),
    }))
    .filter((group) => group.rows.length > 0)

  return (
    <div className="space-y-3">
      {groups.map((group) => (
        <section key={group.kind}>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            {group.heading}
          </h3>
          <ul className="space-y-1">
            {group.rows.map((row) => (
              <li
                key={`${row.type}-${row.key}`}
                className="rounded-md border border-border px-2.5 py-1.5"
              >
                <p className="truncate text-sm text-foreground">{row.label}</p>
                {row.detail && (
                  <p className="truncate text-xs text-muted-foreground">{row.detail}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

/**
 * Search across the whole knowledge base. The term is debounced before it
 * reaches the query — the backend requires a non-empty `q` and caps it at 200
 * characters, and a request per keystroke would be both wrong and slow.
 */
export function KnowledgeSearchBar({
  onQueryChange,
  onTypeChange,
  type,
  showResults = true,
  placeholder = 'Search notes, concepts, resources and bookmarks',
  className,
}: KnowledgeSearchBarProps) {
  const [value, setValue] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced(value.trim())
      onQueryChange?.(value.trim())
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [value, onQueryChange])

  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Input
            value={value}
            maxLength={MAX_QUERY_LENGTH}
            onChange={(event) => setValue(event.target.value)}
            placeholder={placeholder}
            aria-label="Search the knowledge base"
            className="pl-9 pr-9"
          />
          {value !== '' && (
            <button
              type="button"
              onClick={() => setValue('')}
              className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-muted-foreground hover:text-foreground"
            >
              <X aria-hidden="true" className="size-4" />
              <span className="sr-only">Clear search</span>
            </button>
          )}
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
        </div>

        <Select
          value={type ?? ''}
          aria-label="Restrict search to one kind"
          onChange={(event) =>
            onTypeChange?.((event.target.value || undefined) as KnowledgeEntityType | undefined)
          }
          className="sm:w-44"
        >
          <option value="">All kinds</option>
          {KNOWLEDGE_ENTITY_TYPES.map((kind) => (
            <option key={kind} value={kind}>
              {KNOWLEDGE_ENTITY_META[kind].label}
            </option>
          ))}
        </Select>
      </div>

      {showResults && debounced.length >= 2 && (
        <div className="rounded-lg border border-border bg-card p-3">
          <ResultList query={debounced} type={type} />
        </div>
      )}
    </div>
  )
}
