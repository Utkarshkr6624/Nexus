import { CircleAlert, MessageSquareQuote } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { NO_VALUE } from '@/features/analytics/format'
import { RoutingStatusBadge } from '@/features/assistant/components/voice-state-pill'
import { cn } from '@/lib/utils'
import type { VoiceTurn } from '@/features/assistant/types'
import type { IntentName, RoutingDecisionRead, ServiceTargetRead } from '@/types/ml'

/**
 * What the assistant did with what it heard.
 *
 * **NEXO routes; it does not write.** The classifier answers with an intent, a
 * confidence and the validated service behind that intent — never a paragraph —
 * so every line on this surface is written as a *decision*. "Understood as Show
 * my tasks — 97% confident — routes to TaskService.list" describes exactly what
 * came back over the wire. Anything phrased as though the assistant had replied
 * would be copy the payload cannot support, and a reader who then finds no
 * answer has been lied to about the product's central claim.
 *
 * **The two honest gaps are stated, not softened.** `out_of_scope` names the
 * surfaces NEXO does have; `generation_unavailable` says plainly that this needs
 * a free-form answer and that NEXO runs no generative model. Neither is dressed
 * as a fault, because neither is one — the classifier recognised the request
 * correctly in both cases. Only the `intent` field and not the `status` tells
 * you that: `code_assist` with `generation_unavailable` is a correct recognition
 * with nowhere to go, and a UI that branches on `intent` renders it as a failure.
 *
 * **The confidence is written as digits.** There is no meter: the bar would be
 * decoration, and the number is the content — the same rule
 * `risk-score-meter.tsx` states. A percentage a reader wants to compare across
 * turns must be readable, not eyeballed.
 *
 * The intent labels are local because `RoutingDecisionRead` is frozen at nine
 * fields and carries none. They are written as capabilities rather than as
 * class names so the sentence reads "Understood as Show my tasks" instead of
 * exposing the taxonomy; an unknown class falls back to the server's own word
 * rather than rendering nothing.
 */

const INTENT_LABEL: Record<IntentName, string> = {
  account_admin: 'Administer the account',
  analytics_insight: 'Analyse my work data',
  career_track: 'Track my career',
  code_assist: 'Write or explain code',
  deep_reasoning: 'Work through a hard problem',
  developer_intel: 'Investigate a codebase',
  knowledge_capture: 'Save something to knowledge',
  knowledge_lookup: 'Look something up',
  learning_track: 'Plan my learning',
  out_of_scope: 'Outside NEXO',
  project_manage: 'Manage a project',
  risk_query: 'Ask about risks',
  schedule_plan: 'Plan my schedule',
  task_manage: 'Show my tasks',
}

/** The call an accepted intent lands on, with the service named once. */
function routeTarget(target: ServiceTargetRead): string {
  return target.entrypoint || target.service
}

/** A 0-1 probability as a whole percentage, or an em dash if it is not one. */
function percentOf(value: number): string {
  if (!Number.isFinite(value)) return NO_VALUE
  return `${Math.round(Math.min(Math.max(value, 0), 1) * 100)}%`
}

const CODE_CLASS =
  'rounded bg-muted px-1 py-0.5 font-mono text-[0.8em] text-foreground'

export interface RoutingOutcomeProps {
  decision: RoutingDecisionRead
  className?: string
}

/**
 * One routing decision, in the words it deserves.
 *
 * Every branch keeps the same two-part shape — what it was read as, then what
 * NEXO did about it — so the log scans the same way whichever status it holds.
 */
export function RoutingOutcome({ decision, className }: RoutingOutcomeProps) {
  const label = INTENT_LABEL[decision.intent] ?? decision.intent
  const confidence = percentOf(decision.confidence)
  const threshold = percentOf(decision.threshold)
  const target = decision.target

  // `out_of_scope` is itself one of the fourteen classes, so for that status the
  // intent clause would read "Read as Outside NEXO" and repeat the badge.
  const readAs =
    decision.status === 'out_of_scope' && decision.intent === 'out_of_scope' ? null : (
      <>
        {decision.status === 'accepted' ? 'Understood as' : 'Read as'}{' '}
        <strong className="font-semibold text-foreground">{label}</strong> — {confidence}{' '}
        confident.{' '}
      </>
    )

  return (
    <div className={cn('space-y-1.5', className)}>
      <p className="flex flex-wrap items-center gap-2 text-sm leading-relaxed text-muted-foreground">
        <RoutingStatusBadge status={decision.status} />
        <span className="min-w-0">
          {readAs}
          {decision.status === 'accepted' && target && (
            <>
              Routes to <code className={CODE_CLASS}>{routeTarget(target)}</code>.
            </>
          )}
          {decision.status === 'uncertain' && (
            <>
              That is below the {threshold} NEXUS needs before it will route, so nothing was
              named. Rephrase it, or add the words that carry the meaning.
            </>
          )}
          {decision.status === 'out_of_scope' && (
            <>
              NEXUS has no surface for this, so there is nowhere to route it. NEXO does have
              surfaces for tasks and projects, planning, knowledge, analytics, risks and
              recommendations, developer and learning work, career, and account settings.
            </>
          )}
          {decision.status === 'generation_unavailable' && (
            <>
              This request needs a free-form answer. NEXO runs one model — a fourteen-class
              intent classifier — and no generative model, so nothing here can write it. Code
              assistance and deep reasoning both land on this outcome.
            </>
          )}
        </span>
      </p>

      {decision.status === 'uncertain' && decision.alternatives.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Also considered:{' '}
          {decision.alternatives
            .slice(0, 2)
            .map((alternative) => `${INTENT_LABEL[alternative.intent] ?? alternative.intent} (${percentOf(alternative.confidence)})`)
            .join(', ')}
          .
        </p>
      )}

      {decision.reason && <p className="text-xs text-muted-foreground">{decision.reason}</p>}
    </div>
  )
}

/**
 * The transcript and the decision, side by side, for one exchange.
 *
 * Showing the transcript next to the outcome is the difference between a wrong
 * routing and a *visible* wrong routing: when the classifier lands on the wrong
 * service, the sentence that sent it there is right there to disagree with.
 */
function TurnEntry({ turn }: { turn: VoiceTurn }) {
  const spoken = turn.transcript.trim()
  const heard =
    spoken !== '' ? (
      <p className="flex items-start gap-2 text-sm text-foreground">
        <MessageSquareQuote aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0">{spoken}</span>
      </p>
    ) : turn.error ? null : (
      <p className="text-sm italic text-muted-foreground">Nothing was heard.</p>
    )

  const failure = turn.error && (
    <p className="flex items-start gap-2 text-sm text-foreground">
      <CircleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-destructive" />
      <span className="min-w-0">
        {turn.error.message}
        {turn.error.retryable ? ' Ask again when you are ready.' : ''}
      </span>
    </p>
  )

  const when = Number.isFinite(turn.at) ? new Date(turn.at) : null
  const time = when
    ? new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(when)
    : null

  return (
    <li className="space-y-2 py-4 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          {spoken !== '' || turn.error ? 'You said' : 'Turn'}
        </p>
        {time && <p className="tabular-nums text-[11px] text-muted-foreground">{time}</p>}
      </div>
      {heard}
      {failure}
      {turn.decision && <RoutingOutcome decision={turn.decision} />}
    </li>
  )
}

export interface ConversationLogProps {
  /** Oldest first, as the hook keeps them. */
  turns: VoiceTurn[]
  className?: string
}

/**
 * Every turn of this session, in the order it happened.
 *
 * **Nothing leaves this list.** The classifier sees one utterance at a time, so
 * the transcript lives here, in the browser, and only the utterance currently
 * being classified ever crosses the network. Keeping the full text on screen is
 * also what makes that claim checkable rather than merely asserted.
 */
export function ConversationLog({ turns, className }: ConversationLogProps) {
  if (turns.length === 0) {
    return (
      <div className={cn('rounded-lg border border-border', className)}>
        <EmptyState
          icon={MessageSquareQuote}
          compact
          title="No requests yet"
          description="Speak or type a request and the decision NEXUS reached will appear here — the intent it heard, how confident it was, and which service it names. Only the single request is sent; this history stays in your browser."
        />
      </div>
    )
  }

  return (
    <div className={cn('rounded-lg border border-border bg-card', className)}>
      <ol className="divide-y divide-border px-4" aria-label="Requests in this session">
        {turns.map((turn) => (
          <TurnEntry key={turn.id} turn={turn} />
        ))}
      </ol>
    </div>
  )
}
