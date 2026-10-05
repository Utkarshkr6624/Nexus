import { useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight } from 'lucide-react'
import { CircleAlert, Keyboard, MicVocal, ShieldAlert, Trash2, Undo2, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { ConversationLog, RoutingOutcome } from '@/features/assistant/components/conversation-log'
import { RecordButton } from '@/features/assistant/components/record-button'
import { VoiceStatePill } from '@/features/assistant/components/voice-state-pill'
import { RECOGNITION_PRIVACY_NOTICE } from '@/features/assistant/speech-recognition'
import { destinationLink } from '@/features/assistant/destination-route'
import { useVoiceAssistant } from '@/features/assistant/use-voice-assistant'
import { cn } from '@/lib/utils'
import type { VoiceState } from '@/features/assistant/types'

/**
 * The voice interface over NEXO's single intent classifier.
 *
 * **What this panel is allowed to claim.** The model behind it is a fourteen-class
 * classifier, not a language model. It hears one utterance and returns an intent,
 * a confidence and the validated NEXUS service behind it. So the panel says
 * "Understood as Show my tasks — 97% confident — routes to TaskService.list" and
 * never a sentence in the assistant's own voice. There is nothing behind this
 * button that can answer a question, and copy implying otherwise would turn the
 * product's one real limitation into a lie.
 *
 * **The history never leaves this page.** Every turn is rendered here so the
 * reader can see exactly what was classified, and the only text that crosses the
 * network is the utterance being classified right now. A classifier with no
 * dialogue state cannot use a transcript, so sending one would cost the user's
 * privacy and buy nothing.
 *
 * **Typing is not a fallback afterthought — it is the accessible path.** Voice
 * alone locks out anyone who cannot or will not speak aloud, and fails outright
 * in Firefox, which ships no recogniser. So the field is always present and
 * always works, including in the `unsupported` state where it is the *only* way
 * in and is labelled as such.
 *
 * **The microphone disclosure is on the screen, not in a privacy policy.** The
 * words are the canonical `RECOGNITION_PRIVACY_NOTICE`, imported rather than
 * retyped, so this panel cannot drift from the code that makes the promise.
 */

const STATE_ANNOUNCEMENT: Record<VoiceState, string> = {
  unsupported: '',
  idle: '',
  listening: 'Listening.',
  processing: 'Working out which part of NEXO you meant.',
  speaking: 'Reading the outcome aloud.',
  error: '',
}

/**
 * One short sentence for the live region, never the state's whole vocabulary.
 *
 * The state is already on screen in the pill; what the region adds is the change,
 * so it stays short. `idle` is silent because "ready" is the absence of news,
 * `unsupported` is silent because its explanation is rendered permanently and in
 * full directly beneath — announcing a paragraph the reader is already looking
 * at helps nobody — and `error` is silent here because the failure renders as an
 * `alert` next to the control that caused it. Announcing any of those twice is
 * noise, not redundancy.
 */
function announcementFor(state: VoiceState, errorMessage: string | null): string {
  if (state === 'error') return errorMessage ?? ''
  return STATE_ANNOUNCEMENT[state]
}

/** The honest, terminal explanation for a browser that ships no recogniser. */
function UnsupportedNotice() {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/[0.06] p-4">
      <Keyboard aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning" />
      <div className="min-w-0 space-y-1.5">
        <p className="text-sm font-medium text-foreground">
          This browser has no speech recogniser
        </p>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Speech recognition is a Chrome and Edge feature. Firefox does not implement it at all,
          and NEXO will not ask you to install anything to work around that — type your request
          below instead, and it is classified exactly as speech would be.
        </p>
        <p className="text-sm leading-relaxed text-muted-foreground">
          A local recogniser would have to be a speech model of our own, and NEXO runs exactly
          one model: a fourteen-class intent classifier. Shipping a second is not on the table, so
          this is said plainly rather than worked around.
        </p>
      </div>
    </div>
  )
}

/** The failure, with only the answers that are actually true of it. */
function ErrorNotice({
  message,
  retryable,
  onRetry,
  onDismiss,
}: {
  message: string
  retryable: boolean
  onRetry: () => void
  onDismiss: () => void
}) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.04] p-4"
    >
      <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-sm leading-relaxed text-foreground">{message}</p>
        <div className="flex flex-wrap gap-2">
          {retryable && (
            <Button type="button" size="sm" variant="outline" onClick={onRetry}>
              <Undo2 aria-hidden="true" />
              Try that again
            </Button>
          )}
          <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
            <X aria-hidden="true" />
            Dismiss
          </Button>
        </div>
      </div>
    </div>
  )
}

export interface VoiceAssistantProps {
  className?: string
}

export function VoiceAssistant({ className }: VoiceAssistantProps) {
  const navigate = useNavigate()
  const {
    state,
    error,
    interimTranscript,
    suggestion,
    turns,
    lastAction,
    startListening,
    stopListening,
    submitTranscript,
    retry,
    dismissError,
    clearConversation,
  } = useVoiceAssistant()

  const [draft, setDraft] = useState('')

  const unsupported = state === 'unsupported'
  const interim = state === 'listening' ? interimTranscript.trim() : ''
  const announcement = announcementFor(state, error?.message ?? null)
  const newest = turns.length > 0 ? (turns[turns.length - 1] ?? null) : null
  const lastDecision = newest?.decision ?? null

  // Only an accepted turn names a destination; the others have nowhere to send
  // the reader, which is exactly what the decision text already says.
  const link =
    lastDecision?.status === 'accepted'
      ? destinationLink(lastAction?.destination ?? lastDecision.destination)
      : null

  // `submitTranscript` is a no-op outside `idle` and `error`, so the field is
  // disabled wherever it is: offering an input that silently swallows text is
  // worse than not offering it. The two exceptions are deliberate. `error` is a
  // resting state rather than a busy one, and the hook accepts a typed turn from
  // it — after a recognition failure, typing the request is the obvious recovery
  // and blocking it would send the user to a retry button for something they can
  // simply say in text. `unsupported` is the other, where the lifecycle is still
  // idle because no recogniser is what would have moved it, and typing is the
  // only way in.
  const canSubmit = state === 'idle' || state === 'error' || unsupported
  const submitDisabled = draft.trim() === '' || !canSubmit

  // Driven by the submit handler rather than by an effect: React Compiler treats a
  // synchronous `setState` inside `useEffect` as a render-phase update.
  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    const text = draft.trim()
    if (text === '') return
    submitTranscript(text)
    setDraft('')
  }

  return (
    <div className={cn('space-y-4', className)}>
      {/* Mounted empty and never unmounted. A live region that arrives together
          with its first message is frequently never announced at all. */}
      <p aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </p>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2.5">
            <MicVocal aria-hidden="true" className="size-4 text-muted-foreground" />
            <CardTitle>Ask NEXO</CardTitle>
            <VoiceStatePill state={state} />
          </div>
          <CardDescription>
            NEXO listens to one request and names the part of the product you meant. It classifies
            what you said; it does not write an answer.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {unsupported && <UnsupportedNotice />}

          {state === 'error' && error && (
            <ErrorNotice
              message={error.message}
              retryable={error.retryable}
              onRetry={retry}
              onDismiss={dismissError}
            />
          )}

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <RecordButton state={state} onStart={startListening} onStop={stopListening} />

            {interim !== '' && (
              <p className="min-w-0 flex-1 truncate rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
                <span className="font-medium text-foreground">Heard so far: </span>
                {interim}
              </p>
            )}
          </div>

          <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
            <ShieldAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
            <span>{RECOGNITION_PRIVACY_NOTICE}</span>
          </p>

          <Separator />

          <form className="space-y-2" onSubmit={handleSubmit}>
            <Label htmlFor="voice-assistant-input">
              <span className="flex items-center gap-1.5">
                <Keyboard aria-hidden="true" className="size-3.5 text-muted-foreground" />
                Or type your request
              </span>
            </Label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                id="voice-assistant-input"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Show me my open tasks"
                autoComplete="off"
                disabled={!canSubmit}
                className="flex-1"
              />
              <Button type="submit" disabled={submitDisabled}>
                Classify
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {unsupported
                ? 'This is the only way in on this browser.'
                : 'Only this request is sent. The history below stays in your browser.'}
            </p>
          </form>

          {suggestion && (
            <p className="rounded-md border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">
                You last routed {suggestion.label}.
              </span>{' '}
              NEXO cannot carry context into the next request — the classifier reads one utterance
              and nothing else — so if this is the same request again, say it in those words.
            </p>
          )}
        </CardContent>
      </Card>

      {lastDecision && (
        <Card>
          <CardHeader>
            <CardTitle>Latest decision</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {newest && newest.transcript.trim() !== '' && (
              <p className="text-sm text-foreground">“{newest.transcript.trim()}”</p>
            )}
            <RoutingOutcome decision={lastDecision} />
            {link && (
              /* An accepted turn names a destination. Naming it without offering
                 it is a dead end: the reader is told NEXUS understood the
                 request and then given no way to act on it, which reads as the
                 assistant having done nothing at all. Routing is not execution —
                 the button is the honest next step, and it is labelled as
                 navigation rather than as work NEXUS carried out. */
              <Button type="button" variant="outline" size="sm" onClick={() => navigate(link.to)}>
                Go to {link.label}
                <ArrowRight aria-hidden="true" />
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {/* A section heading, not a second masthead: the route renders the page's
          one `<h1>`, so nothing beneath it may claim another. */}
      <section aria-labelledby="assistant-conversation-heading" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2
            id="assistant-conversation-heading"
            className="text-sm font-semibold tracking-tight text-foreground"
          >
            Conversation
          </h2>
          {turns.length > 0 && (
            <Button type="button" variant="ghost" size="sm" onClick={clearConversation}>
              <Trash2 aria-hidden="true" />
              Clear this conversation
            </Button>
          )}
        </div>
        <ConversationLog turns={turns} />
      </section>
    </div>
  )
}
