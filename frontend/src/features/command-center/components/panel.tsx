/**
 * One panel of the Command Center, with its own loading, empty and error states.
 *
 * **The panel is the failure unit.** The page's contract is that one bad request
 * must not blank the page, and that is only true if the panels do not share a
 * query — so each one owns its states, renders its own `ErrorState` with a retry
 * that asks again, and leaves its neighbours untouched.
 *
 * The state is passed as three plain values rather than a whole query result,
 * because one panel draws on several endpoints and the interesting case is the
 * partial failure: some sources answered, one did not. That is a panel that must
 * render *and* report, not a panel that must either render or fail.
 *
 * States, in the order they are checked:
 *
 * 1. **Pending** → a skeleton of the panel's own shape, so the page does not jump
 *    when the answer arrives.
 * 2. **Error** → `ErrorState`, scoped to this panel, with a retry button. Only
 *    set when *every* source behind the panel failed; a partial failure is
 *    reported inside the panel by the caller, because a reader must be told which
 *    parts of a briefing are missing.
 * 3. **Empty** → the caller's empty state. A zero here is a measurement of
 *    nothing, and the panel says what fills it rather than printing a row of
 *    zeroes or an unexplained dash.
 * 4. Otherwise → the caller's children.
 */
import type { ReactNode } from 'react'
import { Inbox } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { toApiError } from '@/services/errors'
import { ProvenanceBadge } from '@/features/command-center/components/provenance-badge'
import type { Provenance } from '@/features/command-center/priority'

export interface PanelState {
  /** True while nothing has arrived yet. */
  pending: boolean
  /** The failure behind the whole panel, or `null` when it has any data. */
  error: unknown | null
  retry: () => void
  /** True when the answer arrived and there is nothing to show. */
  empty?: boolean
  emptyState?: ReactNode
}

interface PanelSkeletonProps {
  /** Rows of placeholder content, so a list panel reserves the right height. */
  rows?: number
  className?: string
}

/** Placeholder content. Mirrors the panel's own shape, not a generic spinner. */
function PanelSkeleton({ rows = 3, className }: PanelSkeletonProps) {
  return (
    <div className={className} aria-busy="true">
      <div className="space-y-3">
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="space-y-2 rounded-md border border-border/60 p-3">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        ))}
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  )
}

export interface CommandCenterPanelProps {
  title: string
  description?: string
  /** Rendered beside the title. Every panel names where its numbers came from. */
  provenance?: Provenance
  /** Extra chips beside the title, e.g. a count read from the response. */
  badges?: ReactNode
  state: PanelState
  /** Heading level: `h2` for a top-level panel, `h3` inside a rail. */
  level?: 'h2' | 'h3'
  className?: string
  children: ReactNode
}

export function CommandCenterPanel({
  title,
  description,
  provenance,
  badges,
  state,
  level = 'h2',
  className,
  children,
}: CommandCenterPanelProps) {
  const headingId = `command-center-panel-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`

  return (
    // `role="region"` is explicit rather than implied: a card labelled by its
    // own heading is a landmark a screen-reader user can navigate to, and the
    // implicit mapping from `aria-labelledby` on a generic element is one not
    // every accessibility API implements.
    <Card role="region" aria-labelledby={headingId} className={className}>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle id={headingId} level={level}>
            {title}
          </CardTitle>
          {provenance && <ProvenanceBadge provenance={provenance} />}
          {badges}
        </div>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>

      <CardContent>
        {state.pending ? (
          <PanelSkeleton />
        ) : state.error ? (
          <ErrorState
            error={toApiError(state.error)}
            title={`${title} could not load`}
            onRetry={state.retry}
            compact
          />
        ) : state.empty ? (
          state.emptyState ?? (
            <EmptyState
              compact
              icon={Inbox}
              title="Nothing recorded yet"
              description="This panel stays empty until a real endpoint has rows to report."
            />
          )
        ) : (
          children
        )}
      </CardContent>
    </Card>
  )
}
