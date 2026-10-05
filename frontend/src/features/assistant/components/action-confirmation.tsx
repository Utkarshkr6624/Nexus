import { CheckCircle2, CircleAlert, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { ActionProposalRead, ConfirmActionRead, ExtractArgumentRead } from '@/types/ml'
import type { VoiceError } from '@/features/assistant/types'

/**
 * The write half of the assistant, and the one part of it that can change
 * something.
 *
 * **The sentence in the middle is the backend's, not ours.** `proposal.summary`
 * was composed by the proposal layer from the winning intent and the arguments
 * it actually extracted, so "Create a project named 'HelloWorld'." encodes a
 * decision about what was read out of the sentence. This file never reassembles
 * one from `payload`: a client-written summary would be a second description of
 * the same write, free to disagree with the first, and the user would be agreeing
 * to the second one. That is *more* important once deletes exist — the summary
 * naming the row is the only place the user is told which row is about to go.
 *
 * **Confirm and Cancel, and nothing else.** The backend publishes
 * `requires_confirmation: true` as a property rather than a field precisely
 * because there is no path that skips this step, so the dialog offers the two
 * answers and the dialog primitive already owns the keyboard: focus moves into
 * the panel on open, Tab is trapped inside it, Escape and the overlay dismiss,
 * and focus returns to wherever it came from on close. Re-implementing any of
 * that here would be the one thing this file must not do.
 *
 * **A destructive kind is styled as one, and the styling is the warning.** The
 * assistant can delete a row now, and `proposal.destructive` is the backend's own
 * answer to "does this discard something". When it is set, the title says so, a
 * plain sentence says the write cannot be undone, and the confirm button carries
 * the destructive variant — so the one irreversible press in this flow is the
 * one button on screen that looks irreversible. The flag is read from the
 * proposal rather than inferred from `kind`: which kinds are destructive is the
 * backend's spec table, and a client that kept its own copy of that list would
 * eventually disagree with it on exactly the buttons that matter. Nothing is
 * withheld either — Cancel stays enabled and says so, because an irreversibility
 * warning that also removes the way out is a trap, not a safeguard.
 *
 * **A failed confirmation leaves the dialog open.** The `role="alert"` inside it
 * says what went wrong, and both buttons come back: nothing is disabled by a
 * failure except while the request is genuinely in flight, and nothing about
 * this file can leave a reader with no way forward.
 */

/**
 * What a destructive write cannot offer the person reading it.
 *
 * One sentence, said plainly, rather than a hedge. "This cannot be undone" is
 * the fact; everything else on screen is detail.
 */
const IRREVERSIBLE_WARNING = 'This cannot be undone.'

/** One extracted field, shown as the value beside the field it fills. */
function ExtractedArguments({ items }: { items: ExtractArgumentRead[] }) {
  if (items.length === 0) return null

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-foreground">What NEXUS read out of that</p>
      {/* A description list, because the relationship is a genuine one: each
          value belongs to the field named beside it, and a screen reader
          announcing them as separate sentences loses that. */}
      <dl className="space-y-1.5 rounded-md border border-border bg-muted/40 px-3 py-2.5">
        {items.map((argument, index) => (
          <div
            key={`${argument.field}-${index}`}
            className="grid gap-0.5 text-xs leading-relaxed sm:grid-cols-[minmax(0,7rem)_minmax(0,1fr)] sm:gap-2"
          >
            <dt className="min-w-0 break-words font-mono text-muted-foreground">
              {argument.field}
            </dt>
            <dd className="min-w-0 break-words text-foreground">
              {argument.value}
              {/* The span is shown only when it is something other than the value
                  itself. Nearly every extraction runs a rule over the whole
                  sentence, and quoting the utterance back at the user who just
                  typed it is noise rather than provenance. */}
              {argument.matched_text !== argument.value && (
                <span className="block text-muted-foreground">
                  read from “{argument.matched_text}”
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** Softer observations the extractor left behind, e.g. an unresolved date. */
function ExtractedNotes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-foreground">Worth knowing</p>
      <ul className="space-y-1 list-disc pl-4 text-xs leading-relaxed text-muted-foreground">
        {notes.map((note, index) => (
          <li key={`${note}-${index}`}>{note}</li>
        ))}
      </ul>
    </div>
  )
}

export interface ActionConfirmationProps {
  /** The proposal to agree to. The caller renders nothing without one. */
  proposal: ActionProposalRead
  /** True while the confirm request is in flight; disables both answers. */
  confirming: boolean
  /** A failure from proposing *or* confirming. Never blocks Cancel. */
  error: VoiceError | null
  onConfirm: () => void
  onCancel: () => void
}

/**
 * The confirm step. Mounted only while a proposal is pending, so `open` is
 * always true and dismissal is always a cancel.
 *
 * `onOpenChange` ignores a close **while the write is in flight**: an Escape or
 * a stray click at that moment would leave the panel showing nothing about a
 * write the backend may well be carrying out, which is the one outcome worse
 * than waiting. This is the same guard `work/components/confirm-dialog.tsx`
 * applies, and for the same reason — and it matters more for a delete, where
 * losing the panel also loses the last chance to say what was removed.
 */
export function ActionConfirmation({
  proposal,
  confirming,
  error,
  onConfirm,
  onCancel,
}: ActionConfirmationProps) {
  const { destructive } = proposal

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onCancel())}>
      <DialogContent className="max-w-md" showClose={!confirming}>
        <DialogHeader>
          <DialogTitle className={cn(destructive && 'text-destructive')}>
            {destructive ? 'This will delete something' : 'Confirm this action'}
          </DialogTitle>
          {/* The backend's sentence, verbatim — the whole reason this type
              publishes `summary`, and on a delete the only statement of which
              row is going. */}
          <DialogDescription>{proposal.summary}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <ExtractedArguments items={proposal.arguments} />
          <ExtractedNotes notes={proposal.notes} />

          {destructive && (
            /* Not the `role="alert"` styling of a failure below: this is not
               news of a fault, and a live region that announces it would read
               the warning twice, once as it appeared and once with the panel. */
            <div className="rounded-md border border-destructive/30 bg-destructive/[0.04] px-3 py-2">
              <p className="text-xs font-medium text-destructive">{IRREVERSIBLE_WARNING}</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {proposal.target_label
                  ? `“${proposal.target_label}” will be deleted permanently. There is no undo and no copy left behind.`
                  : 'The row this refers to will be deleted permanently. There is no undo and no copy left behind.'}
              </p>
            </div>
          )}

          <p className="text-xs leading-relaxed text-muted-foreground">
            NEXUS will check that your account is allowed to do this, and will not write anything
            until you press Confirm. Cancelling leaves everything as it was.
          </p>

          {error && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/[0.04] px-3 py-2"
            >
              <CircleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-destructive" />
              <p className="text-xs leading-relaxed text-foreground">{error.message}</p>
            </div>
          )}
        </div>

        <DialogFooter>
          {/* Cancel stays a ghost button and stays enabled either way. A
              destructive dialog that disabled the way out would be steering the
              reader rather than informing them. */}
          <Button type="button" variant="ghost" onClick={onCancel} disabled={confirming}>
            Cancel
          </Button>
          <Button
            type="button"
            variant={destructive ? 'destructive' : 'default'}
            onClick={onConfirm}
            disabled={confirming}
          >
            {confirming ? 'Working…' : destructive ? 'Delete it' : 'Confirm'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export interface ActionOutcomeProps {
  outcome: ConfirmActionRead
  onDismiss: () => void
  className?: string
}

/**
 * What actually happened, in the service's own sentence.
 *
 * **`applied: false` is shown as a success, not a failure.** A replayed confirm
 * answers `no_op` with the row already there, and the message says so in as many
 * words ("NEXO did not create a second one"). Rendering that as an error would
 * report a working system as broken for doing exactly the right thing.
 *
 * No `role` here: the panel's own polite live region announces this message, and
 * a second live region would read it out twice.
 */
export function ActionOutcome({ outcome, onDismiss, className }: ActionOutcomeProps) {
  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-lg border border-success/30 bg-success/[0.06] p-3',
        className,
      )}
    >
      <CheckCircle2 aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-success" />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-sm leading-relaxed text-foreground">{outcome.message}</p>
        <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
          <X aria-hidden="true" />
          Dismiss
        </Button>
      </div>
    </div>
  )
}

export interface ActionFailureProps {
  error: VoiceError
  onDismiss: () => void
  className?: string
}

/**
 * The action layer failing on its own.
 *
 * Deliberately **not** the panel's `ErrorNotice`: that one replaces the
 * lifecycle with `error`, and here the routing decision above is still true and
 * still correct. Saying the turn failed would retract a good answer because an
 * optional second question was refused, so this is a separate, calmer notice that
 * sits beside the decision rather than over it. It is a `role="alert"` because
 * there is no live region reading it out — the panel's region is reserved for
 * the state change and for a successful write.
 */
export function ActionFailure({ error, onDismiss, className }: ActionFailureProps) {
  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.04] p-3',
        className,
      )}
    >
      <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-sm leading-relaxed text-foreground">{error.message}</p>
        <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
          <X aria-hidden="true" />
          Dismiss
        </Button>
      </div>
    </div>
  )
}