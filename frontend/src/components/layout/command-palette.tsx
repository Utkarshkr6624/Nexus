import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { CornerDownLeft, FileSearch, Search } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Kbd } from '@/components/ui/kbd'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ALL_NAV_ITEMS, NAV_GROUPS } from '@/features/modules/catalog'
import type { ModuleDefinition } from '@/features/modules/catalog'
import { trapFocus, makeBackgroundInert } from '@/features/command-palette/a11y'
import { buildPaletteModel } from '@/features/command-palette/entries'
import type { PaletteEntry, PaletteGroup } from '@/features/command-palette/entries'
import { SEARCH_KIND_LABELS, hitTargetPath } from '@/features/command-palette/search'
import { useSearchHits } from '@/features/command-palette/use-search-hits'
import { QUICK_ACTIONS } from '@/features/quick-actions/quick-actions'
import type { QuickActionDefinition } from '@/features/quick-actions/quick-actions'
import { QuickActionForm } from '@/features/quick-actions/quick-action-form'
import { closeCommandPalette } from '@/features/command-palette/command-palette-store'
import { useDebouncedValue } from '@/hooks/use-debounce'
import { useCommandPalette, usesCommandKey } from '@/hooks/use-command-palette'
import { useThemeStore } from '@/stores/theme-store'
import { cn } from '@/lib/utils'

/** Stable ids so the input can point the screen reader at the active option. */
const LISTBOX_ID = 'command-palette-listbox'

function optionId(index: number): string {
  return `${LISTBOX_ID}-option-${index}`
}

function groupLabelFor(item: ModuleDefinition): string {
  const group = NAV_GROUPS.find((candidate) =>
    candidate.items.some((candidateItem) => candidateItem.to === item.to),
  )
  return group?.label ?? 'Platform'
}

/** Two-line summary per row kind, so the three groups read as one list. */
function rowText(entry: PaletteEntry): { label: string; detail: string; meta?: string } {
  if (entry.kind === 'navigate') {
    return { label: entry.module.label, detail: entry.module.summary, meta: groupLabelFor(entry.module) }
  }
  if (entry.kind === 'action') {
    return { label: entry.action.label, detail: entry.action.description }
  }
  const parts = [entry.hit.snippet, entry.hit.relative_date].filter(Boolean)
  if (entry.hit.project_name) parts.push(`in ${entry.hit.project_name}`)
  return {
    label: entry.hit.title,
    detail: parts.join(' · '),
    meta: SEARCH_KIND_LABELS[entry.hit.kind],
  }
}

interface PaletteRowProps {
  entry: PaletteEntry
  index: number
  active: boolean
  onHover: () => void
  onActivate: () => void
}

function PaletteRow({ entry, index, active, onHover, onActivate }: PaletteRowProps) {
  const Icon =
    entry.kind === 'navigate'
      ? entry.module.icon
      : entry.kind === 'action'
        ? entry.action.icon
        : FileSearch
  const { label, detail, meta } = rowText(entry)

  return (
    // Not focusable: the input keeps DOM focus and names the active option
    // through `aria-activedescendant`, which is the combobox pattern.
    <div
      id={optionId(index)}
      role="option"
      aria-selected={active}
      data-active={active}
      data-kind={entry.kind}
      onMouseMove={onHover}
      onClick={onActivate}
      className={cn(
        'flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left transition-colors duration-150 ease-out',
        active ? 'bg-accent text-accent-foreground' : 'text-foreground',
      )}
    >
      <span
        className={cn(
          'flex size-8 shrink-0 items-center justify-center rounded-md border border-border',
          active ? 'bg-background text-primary' : 'bg-muted text-muted-foreground',
        )}
      >
        <Icon className="size-4" aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{label}</span>
          {meta && (
            <span className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              {meta}
            </span>
          )}
        </span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">{detail}</span>
      </span>
      {active && (
        <CornerDownLeft className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      )}
    </div>
  )
}

interface PaletteResultsProps {
  groups: PaletteGroup[]
  indexOf: (entry: PaletteEntry) => number
  activeIndex: number
  onHover: (index: number) => void
  onActivate: (entry: PaletteEntry) => void
}

function PaletteResults({ groups, indexOf, activeIndex, onHover, onActivate }: PaletteResultsProps) {
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>('[data-active="true"]')
    node?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  return (
    <div
      ref={listRef}
      id={LISTBOX_ID}
      role="listbox"
      aria-label="Commands, destinations and records"
      className="p-1.5"
    >
      {groups.map((group) => (
        <div key={group.id} role="group" aria-label={group.label}>
          {/* Decorative: the group's `aria-label` already carries this word for
              a screen reader, so announcing it twice would be noise. */}
          <div
            aria-hidden="true"
            className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground first:pt-1"
          >
            {group.label}
          </div>
          {group.entries.map((entry) => {
            const index = indexOf(entry)
            return (
              <PaletteRow
                key={entry.id}
                entry={entry}
                index={index}
                active={index === activeIndex}
                onHover={() => onHover(index)}
                onActivate={() => onActivate(entry)}
              />
            )
          })}
        </div>
      ))}
    </div>
  )
}

function PaletteEmptyState({ searching }: { searching: boolean }) {
  if (searching) {
    return (
      <div className="px-4 py-10 text-center">
        <p className="text-sm font-medium text-foreground">Searching your records…</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Looking across projects, tasks, notes, goals and more.
        </p>
      </div>
    )
  }
  return (
    <div className="px-4 py-10 text-center">
      <p className="text-sm font-medium text-foreground">Nothing matches</p>
      <p className="mt-1 text-sm text-muted-foreground">
        Try a module name, a command like “new task”, or a word from a record.
      </p>
    </div>
  )
}

/**
 * Mounted only while the palette is open, so the query and selection start
 * clean on every invocation without an effect to reset them.
 */
function PaletteDialog() {
  const navigate = useNavigate()
  const resolvedTheme = useThemeStore((state) => state.resolvedTheme)
  const setTheme = useThemeStore((state) => state.setTheme)

  const [query, setQuery] = useState('')
  const [selection, setSelection] = useState({ key: '', index: 0 })
  const [pendingAction, setPendingAction] = useState<QuickActionDefinition | null>(null)
  const debouncedQuery = useDebouncedValue(query, 120)
  const inputRef = useRef<HTMLInputElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  // The element focused before the palette opened; restored on unmount.
  const previouslyFocused = useRef<HTMLElement | null>(
    typeof document === 'undefined' ? null : (document.activeElement as HTMLElement | null),
  )

  const search = useSearchHits(query)

  const model = useMemo(
    () =>
      buildPaletteModel({
        query: debouncedQuery,
        actions: QUICK_ACTIONS,
        modules: ALL_NAV_ITEMS,
        groupLabelFor,
        hits: search.hits,
      }),
    [debouncedQuery, search.hits],
  )

  const indexById = useMemo(
    () => new Map(model.entries.map((entry, index) => [entry.id, index] as const)),
    [model.entries],
  )
  const indexOf = useCallback(
    (entry: PaletteEntry) => indexById.get(entry.id) ?? 0,
    [indexById],
  )

  /**
   * The active index is only meaningful against one exact list. The key carries
   * the query *and* the row identities, so hits landing from the network reset
   * the cursor to the top instead of leaving it pointing at whatever row slid
   * into that position.
   */
  const selectionKey = `${debouncedQuery}|${model.entries.map((entry) => entry.id).join('|')}`
  const activeIndex =
    selection.key === selectionKey
      ? Math.min(selection.index, Math.max(model.entries.length - 1, 0))
      : 0

  // The "Nothing matches" branch renders no listbox at all.
  const hasResults = !model.empty

  const setActiveIndex = useCallback(
    (index: number) => setSelection({ key: selectionKey, index }),
    [selectionKey],
  )

  const toggleTheme = useCallback(
    () => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark'),
    [resolvedTheme, setTheme],
  )

  const activate = useCallback(
    (entry: PaletteEntry) => {
      if (entry.kind === 'navigate') {
        closeCommandPalette()
        navigate(entry.module.to)
        return
      }
      if (entry.kind === 'hit') {
        const target = hitTargetPath(entry.hit)
        // A kind with no page of its own cannot be navigated to. Every kind the
        // endpoint currently returns has one, so this is a guard against a
        // future kind rather than a path a user can reach today — and it closes
        // the palette either way rather than leaving it open on a dead row.
        closeCommandPalette()
        if (target) navigate(target)
        return
      }
      const action = entry.action
      // A creating action opens its form instead of writing: nothing is posted
      // until the person in that form presses the button.
      if (action.form) {
        setPendingAction(action)
        return
      }
      if (action.navigateTo) {
        closeCommandPalette()
        navigate(action.navigateTo)
        return
      }
      if (action.toggleTheme) {
        toggleTheme()
        closeCommandPalette()
      }
    },
    [navigate, toggleTheme],
  )

  useEffect(() => {
    const frame = requestAnimationFrame(() => inputRef.current?.focus())
    return () => cancelAnimationFrame(frame)
  }, [])

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [])

  // Modal: the page behind cannot be tabbed to, read, or clicked through. Both
  // teardowns run on unmount, so closing the palette gives the page back intact.
  useEffect(() => {
    const overlay = overlayRef.current
    const dialog = dialogRef.current
    if (!overlay || !dialog) return undefined
    const restoreInert = makeBackgroundInert(overlay)
    const releaseTrap = trapFocus(dialog)
    return () => {
      releaseTrap()
      restoreInert()
    }
  }, [])

  useEffect(() => {
    const restoreFocusTo = previouslyFocused.current
    return () => {
      restoreFocusTo?.focus?.()
    }
  }, [])

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeCommandPalette()
      return
    }
    // With a form open the keys belong to the form: Enter submits it, and the
    // arrow keys move between its fields.
    if (pendingAction) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      const total = model.entries.length
      setActiveIndex(total === 0 ? 0 : (activeIndex + 1) % total)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      const total = model.entries.length
      setActiveIndex(total === 0 ? 0 : (activeIndex - 1 + total) % total)
      return
    }
    if (event.key === 'Enter') {
      const target = model.entries[activeIndex]
      if (target) {
        event.preventDefault()
        activate(target)
      }
    }
  }

  const modKey = usesCommandKey() ? '⌘' : 'Ctrl'
  // Covers both halves of the wait: the term settling behind the debounce and
  // the request being in flight. Either way, an empty list would be a lie.
  const searching = !hasResults && search.pending

  return createPortal(
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 animate-in fade-in-0 duration-150 ease-out"
      onKeyDown={onKeyDown}
    >
      <button
        type="button"
        aria-label="Close command palette"
        // A backdrop is a pointer affordance, not a stop in the tab ring: the
        // dialog's own controls are what a keyboard user cycles through.
        tabIndex={-1}
        onClick={closeCommandPalette}
        className="absolute inset-0 h-full w-full cursor-default bg-foreground/20 backdrop-blur-[1px]"
      />

      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className={cn(
          'absolute left-1/2 top-[12vh] w-[min(560px,calc(100vw-32px))] -translate-x-1/2',
          'animate-in fade-in-0 zoom-in-95 overflow-hidden rounded-lg border border-border bg-popover shadow-lg duration-150 ease-out',
        )}
      >
        {pendingAction ? (
          <QuickActionForm
            action={pendingAction}
            onDone={closeCommandPalette}
            onCancel={() => setPendingAction(null)}
          />
        ) : (
          <>
            <div className="flex items-center gap-2.5 border-b border-border px-3.5">
              <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Jump to a module, run a command, or search…"
                aria-label="Filter commands, destinations and records"
                role="combobox"
                aria-expanded={hasResults}
                // Both are only meaningful against a rendered listbox. The
                // "Nothing matches" branch has none, so pointing at the id
                // would leave `aria-controls` and `aria-activedescendant`
                // naming an element that does not exist.
                aria-controls={hasResults ? LISTBOX_ID : undefined}
                aria-autocomplete="list"
                aria-activedescendant={hasResults ? optionId(activeIndex) : undefined}
                className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              />
              <Badge variant="outline" className="hidden shrink-0 font-normal sm:inline-flex">
                {model.entries.length} results
              </Badge>
            </div>

            <ScrollArea className="max-h-[min(360px,50vh)]">
              {hasResults ? (
                <PaletteResults
                  groups={model.groups}
                  indexOf={indexOf}
                  activeIndex={activeIndex}
                  onHover={setActiveIndex}
                  onActivate={activate}
                />
              ) : (
                <PaletteEmptyState searching={searching} />
              )}
            </ScrollArea>

            <div className="flex items-center gap-3 border-t border-border px-3.5 py-2 text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1">
                <Kbd>↑</Kbd>
                <Kbd>↓</Kbd> navigate
              </span>
              <span className="flex items-center gap-1">
                <Kbd>↵</Kbd> open or run
              </span>
              <span className="hidden items-center gap-1 sm:flex">
                <Kbd>Esc</Kbd> close
              </span>
              <span className="ml-auto flex items-center gap-1">
                <Kbd>{modKey}</Kbd>
                <Kbd>K</Kbd>
              </span>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}

/**
 * Global command palette, opened with ⌘K / Ctrl+K.
 *
 * One list, three kinds of answer: quick actions that perform real requests,
 * the destinations from the module catalog, and whatever `GET /api/v1/search`
 * returns for the words typed. They share a single index space, so the arrow
 * keys, `aria-activedescendant` and the empty state behave identically whichever
 * group the cursor is in.
 */
export function CommandPalette() {
  const { open } = useCommandPalette()
  return open ? <PaletteDialog /> : null
}
