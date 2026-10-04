import type { LucideIcon } from 'lucide-react'
import { Ban, CircleCheck, CircleHelp, Loader2, Mic, Radio, TriangleAlert, Volume2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import { cn } from '@/lib/utils'
import type { VoiceState } from '@/features/assistant/types'
import type { RoutingStatus } from '@/types/ml'

/**
 * The assistant's vocabulary chips.
 *
 * **Every chip here carries an icon *and* the word**, which is the same contract
 * `SeverityBadge` discharges on the risk surface. The lifecycle of a voice
 * assistant is six states that differ mainly in *motion and hue* — a dot that
 * pulses, a dot that stops — so it is exactly the place where a colour-only
 * indicator would be first to fail: in dark mode, to a reader with a colour
 * vision deficiency, and to a screen reader, for which a pulsing span is silence.
 * The icons therefore differ in **shape**, and the word is the authority.
 *
 * `idle` and `listening` would otherwise be the same microphone, so the pill
 * swaps to a broadcast glyph while it listens: that is the one pair a reader
 * genuinely cannot tell apart from a word, and the word is small.
 *
 * Both lookups route their variant through `TONE_BADGE_VARIANT`, so no call site
 * names a `Badge` variant and the two chip families cannot drift apart. They
 * stay module-private for the same reason `risk-vocabulary` keeps its tables out
 * of the components that read them: a `.tsx` file that exports a lookup beside
 * its components breaks fast refresh, and the lookup is an implementation detail
 * of these two chips anyway. Assert against what they render.
 */

export interface VoiceStateMeta {
  /** The word a reader sees, and the authority on what the state is. */
  label: string
  icon: LucideIcon
  tone: keyof typeof TONE_BADGE_VARIANT
  /** Tooltip: what the state means, for the reader who wants the detail. */
  description: string
}

const VOICE_STATE_META: Record<VoiceState, VoiceStateMeta> = {
  unsupported: {
    label: 'Speech input unavailable',
    icon: Ban,
    tone: 'warning',
    description:
      'This browser ships no speech recogniser. NEXO still classifies typed requests.',
  },
  idle: {
    label: 'Tap to speak',
    icon: Mic,
    tone: 'neutral',
    description: 'Ready. Press the button, or type below, to classify one request.',
  },
  listening: {
    label: 'Listening…',
    icon: Radio,
    tone: 'info',
    description: 'The microphone is open. Stop before the deadline to send what was heard.',
  },
  processing: {
    label: 'Processing…',
    icon: Loader2,
    tone: 'info',
    description: 'Your request is being classified into one of fourteen intents.',
  },
  speaking: {
    label: 'Speaking…',
    icon: Volume2,
    tone: 'info',
    description: 'The outcome is being read aloud.',
  },
  error: {
    label: 'Could not finish that',
    icon: TriangleAlert,
    tone: 'danger',
    description: 'The last turn failed. Nothing was changed.',
  },
}

const ROUTING_STATUS_META: Record<RoutingStatus, VoiceStateMeta> = {
  accepted: {
    label: 'Routed',
    icon: CircleCheck,
    tone: 'success',
    description: 'Confident enough to name a NEXUS service.',
  },
  uncertain: {
    label: 'Not confident enough',
    icon: CircleHelp,
    tone: 'warning',
    description: 'A prediction came back below the threshold NEXUS needs to act.',
  },
  out_of_scope: {
    label: 'Out of scope',
    icon: Ban,
    tone: 'neutral',
    description: 'NEXUS has no surface for this request.',
  },
  generation_unavailable: {
    label: 'Needs generation',
    icon: TriangleAlert,
    tone: 'warning',
    description: 'This request needs free-form generation. NEXO runs no generative model.',
  },
}

export interface VoiceStatePillProps {
  state: VoiceState
  className?: string
}

/**
 * The lifecycle, as a shape-distinct icon plus the word.
 *
 * `processing` renders the shared `Spinner` rather than its `Loader2` so the
 * motion that means "working" is the same motion everywhere in the product. The
 * spinner carries no screen-reader text of its own here: the badge beside it
 * already reads "Processing…", and a second label inside the same chip is a
 * duplicated announcement rather than extra information.
 */
export function VoiceStatePill({ state, className }: VoiceStatePillProps) {
  const reducedMotion = usePrefersReducedMotion()
  const meta = VOICE_STATE_META[state]
  const Icon = meta.icon
  const animated = state === 'listening' && !reducedMotion

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[meta.tone]}
      className={cn('gap-1.5', className)}
      title={meta.description}
    >
      {state === 'processing' ? (
        <Spinner size="sm" label="" />
      ) : (
        <Icon aria-hidden="true" className={cn('size-3', animated && 'animate-pulse')} />
      )}
      {meta.label}
    </Badge>
  )
}

export interface RoutingStatusBadgeProps {
  status: RoutingStatus
  className?: string
}

/**
 * What NEXUS did with the utterance — and here the word really is the message.
 *
 * `generation_unavailable` in particular reads as a warning without any
 * explanation, so it is deliberately *not* an error badge: nothing failed, the
 * classifier did its job, and the reason it has nowhere to send the request is
 * printed beside it in full. Colouring it `danger` would report a product
 * decision as a fault.
 */
export function RoutingStatusBadge({ status, className }: RoutingStatusBadgeProps) {
  const meta = ROUTING_STATUS_META[status]
  const Icon = meta.icon

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[meta.tone]}
      className={cn('gap-1', className)}
      title={meta.description}
    >
      <Icon aria-hidden="true" className="size-3" />
      {meta.label}
    </Badge>
  )
}
