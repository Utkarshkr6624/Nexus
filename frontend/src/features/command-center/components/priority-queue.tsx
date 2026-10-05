/**
 * The ranked queue: the page's answer to "what should I do next".
 *
 * **Every row shows its arithmetic.** A number a reader cannot check is a
 * number they have to take on trust, so each row expands into the factors that
 * produced it — the points, the ceiling each was measured against, and the
 * sentence that says why that bucket and not the next one down. The rule itself
 * and its three stated caveats are printed once, above the list, so the reader
 * knows which parts of the number were measured and which were chosen here.
 *
 * **The ordering is not the model's work.** NEXUS runs one model, an intent
 * classifier, and it never sees a deadline. The ranking is the arithmetic in
 * `../priority`, and the panel says so.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight, ListChecks, ShieldQuestion } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { PRIORITY_CAVEATS, PRIORITY_RULE, SIGNAL_KIND_LABEL } from '@/features/command-center/priority'
import { ProvenanceBadge } from '@/features/command-center/components/provenance-badge'
import type { PrioritySignal } from '@/features/command-center/priority'

/** How many rows the queue shows before the "show all" control appears. */
const COLLAPSED_ROWS = 5

function ScoreMeter({ signal }: { signal: PrioritySignal }) {
  return (
    <div className="flex shrink-0 items-baseline gap-1">
      <span
        data-priority-score={signal.id}
        className="font-mono text-lg font-semibold tabular-nums text-foreground"
      >
        {signal.priority}
      </span>
      <span className="text-[11px] text-muted-foreground">/100</span>
    </div>
  )
}

function FactorList({ signal }: { signal: PrioritySignal }) {
  return (
    <table className="w-full text-left text-xs">
      <caption className="sr-only">
        How {signal.title} scored {signal.priority} out of 100
      </caption>
      <thead>
        <tr className="text-muted-foreground">
          <th scope="col" className="w-40 pb-1 font-medium">
            Factor
          </th>
          <th scope="col" className="w-20 pb-1 font-medium">
            Points
          </th>
          <th scope="col" className="pb-1 font-medium">
            Why
          </th>
        </tr>
      </thead>
      <tbody>
        {signal.factors.map((factor) => (
          <tr key={factor.label} className="align-top">
            <th scope="row" className="py-1 pr-3 font-medium text-foreground">
              {factor.label}
            </th>
            <td className="py-1 pr-3 font-mono tabular-nums text-muted-foreground">
              {factor.points}/{factor.ceiling}
            </td>
            <td className="py-1 text-muted-foreground">{factor.why}</td>
          </tr>
        ))}
        <tr className="border-t border-border/60 align-top">
          <th scope="row" className="py-1.5 pr-3 font-medium text-foreground">
            Total
          </th>
          <td className="py-1.5 pr-3 font-mono tabular-nums text-muted-foreground">
            {signal.raw}/{signal.ceiling}
          </td>
          <td className="py-1.5 text-muted-foreground">
            Normalised to {signal.priority} of 100. A calculated ranking, not a measurement.
          </td>
        </tr>
      </tbody>
    </table>
  )
}

function SignalRow({ signal }: { signal: PrioritySignal }) {
  const [open, setOpen] = useState(false)
  const panelId = `factors-${signal.id.replace(/[^a-z0-9]+/gi, '-')}`

  return (
    <li className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2 p-4">
        <ScoreMeter signal={signal} />

        <div className="min-w-[14rem] flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-normal">
              {SIGNAL_KIND_LABEL[signal.kind]}
            </Badge>
            <Badge variant="outline" className="capitalize font-normal">
              {signal.band}
            </Badge>
          </div>
          <p data-priority-title={signal.id} className="text-sm font-medium leading-snug text-foreground">
            {signal.title}
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">{signal.detail}</p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((previous) => !previous)}
          >
            {open ? (
              <ChevronDown aria-hidden="true" />
            ) : (
              <ChevronRight aria-hidden="true" />
            )}
            {open ? 'Hide' : 'Why'}
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link to={signal.href}>Open</Link>
          </Button>
        </div>
      </div>

      {open && (
        <div id={panelId} className="border-t border-border/60 bg-muted/30 px-4 py-3">
          <FactorList signal={signal} />
        </div>
      )}
    </li>
  )
}

export interface PriorityQueueProps {
  signals: readonly PrioritySignal[]
  /**
   * The date the goal source is filtered on, for the empty state.
   *
   * The queue reads goals with `target_before`, so a goal with no target date
   * is never in it. The empty state used to say "no active goal is waiting",
   * which reads as a count of every open goal and is not: an account with one
   * undated goal was told nothing was waiting. Naming the date the query used is
   * the only way the sentence is true.
   */
  goalsThrough: string
  className?: string
}

export function PriorityQueue({ signals, goalsThrough, className }: PriorityQueueProps) {
  const [showAll, setShowAll] = useState(false)
  const visible = showAll ? signals : signals.slice(0, COLLAPSED_ROWS)

  if (signals.length === 0) {
    return (
      <EmptyState
        compact
        icon={ListChecks}
        title="Nothing is asking for a decision"
        description={`No live finding, unanswered suggestion, open deadline, schedule conflict or learning goal with a target date on or before ${goalsThrough} is waiting. That is a measurement of the records that exist, not a claim that nothing is wrong.`}
      />
    )
  }

  return (
    <div className={cn('space-y-4', className)}>
      <details className="rounded-md border border-border bg-muted/30 px-3 py-2">
        <summary className="flex cursor-pointer items-center gap-2 text-xs font-medium text-foreground">
          <ShieldQuestion aria-hidden="true" className="size-3.5 text-muted-foreground" />
          How this order is calculated
        </summary>
        <div className="mt-3 space-y-3">
          <ProvenanceBadge provenance="calculated" />
          <p className="text-xs leading-relaxed text-muted-foreground">
            NEXUS runs one model — a fourteen-class intent classifier — and it maps a sentence to
            an intent. It does not see a deadline, so it does not order this list. Every score
            below is arithmetic over numbers a real endpoint returned, and the same records
            always produce the same order.
          </p>
          <ul className="space-y-2">
            {PRIORITY_RULE.map((rule) => (
              <li key={rule.label} className="text-xs leading-relaxed">
                <span className="font-medium text-foreground">{rule.label}</span>
                <span className="text-muted-foreground"> · {rule.applies} — {rule.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      </details>

      <ol className="space-y-3" data-testid="priority-queue">
        {visible.map((signal) => (
          <SignalRow key={signal.id} signal={signal} />
        ))}
      </ol>

      {signals.length > COLLAPSED_ROWS && !showAll && (
        <Button variant="outline" size="sm" onClick={() => setShowAll(true)}>
          Show all {signals.length}
        </Button>
      )}

      {/*
       * The caveats are not behind the disclosure. They are the three sentences
       * that say which parts of the number above were chosen rather than
       * observed, and a rule that is only honest once the reader goes looking
       * for it is not honest in the way the brief means.
       */}
      <ul className="space-y-1.5 border-t border-border pt-4">
        {PRIORITY_CAVEATS.map((caveat) => (
          <li key={caveat} className="text-xs leading-relaxed text-muted-foreground">
            {caveat}
          </li>
        ))}
      </ul>
    </div>
  )
}
