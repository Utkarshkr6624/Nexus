import type { ModuleDefinition } from '@/features/modules/catalog'
import { matchesQuickAction } from '@/features/quick-actions/quick-actions'
import type { QuickActionDefinition } from '@/features/quick-actions/quick-actions'

import type { SearchHit } from './search'

/**
 * One row in the palette, whatever it came from.
 *
 * The three kinds are flattened into a single ordered list before they reach the
 * DOM, and that is what makes `aria-activedescendant` tractable: the active
 * option is an **index into this array**, not a per-group cursor that three
 * components would each have to agree about. Groups are a rendering concern
 * applied afterwards and never a navigation one.
 */
export type PaletteEntry =
  | { kind: 'action'; id: string; action: QuickActionDefinition }
  | { kind: 'navigate'; id: string; module: ModuleDefinition }
  | { kind: 'hit'; id: string; hit: SearchHit }

export interface PaletteGroup {
  id: 'actions' | 'navigate' | 'hits'
  label: string
  entries: PaletteEntry[]
}

export interface PaletteModel {
  groups: PaletteGroup[]
  /** Every group's entries, in render order. The index space the input uses. */
  entries: PaletteEntry[]
  /** True when nothing at all matched — the listbox is then not rendered. */
  empty: boolean
}

/**
 * Naive AND-over-substrings, unchanged from the destination-only palette.
 *
 * The palette is a keyboard surface a person drives at speed, so the filter has
 * to be predictable rather than clever: every whitespace-separated term must
 * appear somewhere in the row. Real relevance ranking is the job of
 * `GET /api/v1/search`, which is the third group.
 */
export function matchesModule(module: ModuleDefinition, query: string, groupLabel: string): boolean {
  if (!query.trim()) return true
  const haystack = [module.label, module.summary, groupLabel, ...module.keywords]
    .join(' ')
    .toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => haystack.includes(term))
}

/**
 * Builds the whole list.
 *
 * Order is fixed and deliberate: quick actions first because they are the only
 * rows that *do* something and therefore the reason to reach for a palette at
 * all, then the destinations the palette has always offered, then the records —
 * the slowest to arrive, so the last place the cursor is likely resting.
 */
export function buildPaletteModel(input: {
  query: string
  actions: readonly QuickActionDefinition[]
  modules: readonly ModuleDefinition[]
  groupLabelFor: (module: ModuleDefinition) => string
  hits: readonly SearchHit[]
}): PaletteModel {
  const { query, actions, modules, groupLabelFor, hits } = input

  const matchingActions = actions.filter((action) => matchesQuickAction(action, query))
  const matchingModules = modules.filter((module) =>
    matchesModule(module, query, groupLabelFor(module)),
  )

  const groups: PaletteGroup[] = []

  if (matchingActions.length > 0) {
    groups.push({
      id: 'actions',
      label: 'Quick actions',
      entries: matchingActions.map((action) => ({
        kind: 'action' as const,
        id: `action:${action.id}`,
        action,
      })),
    })
  }

  if (matchingModules.length > 0) {
    groups.push({
      id: 'navigate',
      label: 'Destinations',
      entries: matchingModules.map((module) => ({
        kind: 'navigate' as const,
        id: `navigate:${module.to}`,
        module,
      })),
    })
  }

  if (hits.length > 0) {
    groups.push({
      id: 'hits',
      label: 'Your records',
      entries: hits.map((hit) => ({ kind: 'hit' as const, id: `hit:${hit.kind}:${hit.id}`, hit })),
    })
  }

  return {
    groups,
    entries: groups.flatMap((group) => group.entries),
    empty: groups.length === 0,
  }
}
