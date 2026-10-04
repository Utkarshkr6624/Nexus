import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface LiveStatusProps {
  /**
   * Whether there is anything to announce.
   *
   * This toggles the *message*, never the element — that is the whole point of
   * the component, and passing it the other way round reintroduces the bug it
   * exists to prevent.
   */
  active: boolean
  children: ReactNode
  /** Styling for the active state. An idle region is left unstyled, and bare. */
  className?: string
}

/**
 * A polite live region that is in the document before it has anything to say.
 *
 * `role="status"` announces a *change to a region that already exists*. The
 * notice this replaces was written as `{busy && <p role="status">Updating…</p>}`,
 * which inserts a brand-new node into the accessibility tree together with its
 * text — and most screen readers stay silent for that, so the one moment the
 * app most needs to say "these rows are the previous answer" is the moment it
 * says nothing. The element here is always mounted and only its content toggles,
 * which is the same shape the toaster and the search results count already use.
 *
 * The styling is applied only while `active` as well. Tailwind's preflight
 * zeroes the paragraph margin, so an empty region collapses to nothing rather
 * than leaving the notice's `mt-3` behind as a strip of dead space on every
 * idle render.
 */
export function LiveStatus({ active, children, className }: LiveStatusProps) {
  return (
    <p role="status" className={cn(active && className)}>
      {active ? children : null}
    </p>
  )
}
