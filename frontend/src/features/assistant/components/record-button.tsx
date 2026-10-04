import type { LucideIcon } from 'lucide-react'
import { Mic, MicOff, Volume2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import { cn } from '@/lib/utils'
import type { VoiceState } from '@/features/assistant/types'

/**
 * The one control that starts and stops a turn.
 *
 * **Recording is destructive, not merely "active".** The palette already owns a
 * red that means "stop this now", and reusing it means the colour means one
 * thing across the product rather than two. Nothing is deleted by pressing it —
 * it ends a live microphone — which is exactly the class of affordance the
 * destructive token is for.
 *
 * **The microphone pulses while it is open, and drops the pulse for readers who
 * asked for reduced motion.** The pulse is the only signal that a device is
 * *currently* capturing audio, and it is therefore the one animation on this
 * surface that carries information rather than decoration — which is precisely
 * why it is the one that has to be suppressible. The state pill still announces
 * the same thing in words, so nothing is lost when the motion is gone.
 */

interface RecordButtonMeta {
  icon: LucideIcon | null
  /** Visible button text; the accessible name below says the same thing. */
  text: string
  label: string
}

const RECORD_BUTTON_META: Record<VoiceState, RecordButtonMeta> = {
  unsupported: {
    icon: MicOff,
    text: 'Speech input unavailable',
    label: 'Speech input is not available in this browser',
  },
  idle: {
    icon: Mic,
    text: 'Tap to speak',
    label: 'Start speaking. Your request is classified, not answered.',
  },
  listening: {
    icon: Mic,
    text: 'Stop listening',
    label: 'Stop listening and classify what was heard',
  },
  processing: {
    // `null` is the instruction to render the shared `Spinner` in its place.
    icon: null,
    text: 'Classifying…',
    label: 'Waiting for the classifier',
  },
  speaking: {
    icon: Volume2,
    text: 'Reading aloud…',
    label: 'Reading the outcome aloud',
  },
  error: {
    icon: Mic,
    text: 'Try again',
    label: 'Try speaking again',
  },
}

export interface RecordButtonProps {
  state: VoiceState
  onStart: () => void
  onStop: () => void
  className?: string
}

/**
 * Start/stop for one utterance.
 *
 * The button is disabled in every state in which a new turn cannot begin, rather
 * than hidden: a control that vanishes between turns makes the surface look
 * broken for the second it is gone, and a disabled control with a matching
 * label explains itself. `error` is deliberately *not* disabled — retrying is the
 * natural next move after a failure, and the copy says so.
 */
export function RecordButton({ state, onStart, onStop, className }: RecordButtonProps) {
  const reducedMotion = usePrefersReducedMotion()
  const meta = RECORD_BUTTON_META[state]
  const listening = state === 'listening'
  const disabled = state === 'unsupported' || state === 'processing' || state === 'speaking'

  const Icon = meta.icon
  const handleClick = listening ? onStop : onStart

  return (
    <Button
      type="button"
      variant={listening ? 'destructive' : 'default'}
      size="lg"
      disabled={disabled}
      onClick={handleClick}
      aria-label={meta.label}
      className={cn('min-w-44', className)}
    >
      {Icon ? (
        <Icon
          aria-hidden="true"
          className={cn('size-4', listening && !reducedMotion && 'animate-pulse')}
        />
      ) : (
        <Spinner size="sm" label="" />
      )}
      {meta.text}
    </Button>
  )
}
