/**
 * The classifier panel: what NEXUS's one model is, and what it last said.
 *
 * **NEXUS runs one model** — `microsoft/deberta-v3-base`, a fourteen-class intent
 * classifier. It maps one utterance to an intent name, a confidence and a
 * routing status. It is not a language model: it cannot generate text, extract
 * arguments, call a tool or tell "delete a task" apart from "create a task",
 * because the taxonomy asks *which surface a request lands on*, not what to do
 * to it. Everything this panel says follows from that, including what it refuses
 * to say.
 *
 * **A prediction is never presented as a fact.** Three separate guards do that
 * work:
 *
 * 1. The panel is badged `model-derived` in its own header.
 * 2. A prediction is rendered inside its own bordered block whose heading repeats
 *    the word "Prediction", and whose copy says in the first sentence that this is
 *    the model guessing what one sentence meant.
 * 3. No figure from a prediction is ever placed beside a measured figure in the
 *    same visual row, so the two can never be read as the same kind of number.
 *
 * `GET /ml/status` answers 200 even when the runtime is degraded, so
 * `available: false` is data this panel renders — "disabled" is a fact about the
 * deployment, not an error state. Only a transport or server failure becomes the
 * panel's `ErrorState`.
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BrainCircuit, CircleOff, Route } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatNumber } from '@/features/analytics/format'
import { routeUtterance } from '@/services/ml'
import { cn } from '@/lib/utils'
import { ProvenanceBadge } from '@/features/command-center/components/provenance-badge'
import { commandCenterKeys } from '@/features/command-center/hooks'
import type { MlStatusRead, RoutingDecisionRead } from '@/types/ml'

/** Confidence is printed as a percentage of the model's own output. */
function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

/**
 * A duration at the precision a reader can hold: `36.827084100000036` → `36.8s`.
 *
 * The wire carries the raw float because it is a timing measurement and the
 * backend has no reason to round it; printing it raw gives a tile seventeen
 * significant figures, which reads as a fault rather than as a number.
 */
function seconds(value: number): string {
  return Number.isFinite(value) ? `${value.toFixed(1)}s` : 'not reported'
}

/**
 * The checkpoint as the last two path segments: `…/small-model/final`.
 *
 * The wire carries the machine's absolute install path — `E:\…\artifacts\
 * small-model\final` on a developer box — which says nothing about this account
 * and is a layout detail of the host. A row reading a drive letter is not
 * information; the artifact's own name is.
 */
function checkpointName(checkpoint: string): string {
  const segments = checkpoint.split(/[\\/]+/).filter(Boolean)
  if (segments.length <= 2) return segments.join('/')
  return `…/${segments.slice(-2).join('/')}`
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-right font-mono text-xs text-foreground">{value}</dd>
    </div>
  )
}

/**
 * One routing decision, framed so it cannot be mistaken for a measurement.
 *
 * `intent` is what the model predicted; `status` is what NEXUS did with that
 * prediction. The two are different facts and a status of
 * `generation_unavailable` still carries a correctly recognised intent — so the
 * status is the headline and the intent is the evidence under it, never the
 * other way round.
 */
function Prediction({ decision }: { decision: RoutingDecisionRead }) {
  return (
    <section
      aria-label="Prediction from the classifier"
      data-testid="ml-prediction"
      className="space-y-3 rounded-md border-2 border-dashed border-primary/50 bg-primary/[0.04] p-4"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-foreground">Prediction</h3>
        <ProvenanceBadge provenance="model-derived" />
      </header>

      <p className="text-xs leading-relaxed text-muted-foreground">
        The classifier guessed what one sentence was asking for. It did not read your work,
        count anything, or check a record — nothing below is a measurement.
      </p>

      <dl className="divide-y divide-border/60 rounded-md border border-border bg-card">
        <Row label="Outcome" value={decision.status} />
        <Row label="Predicted intent" value={decision.intent} />
        <Row label="Confidence" value={percent(decision.confidence)} />
        <Row label="Routing threshold" value={percent(decision.threshold)} />
        <Row label="Destination" value={decision.destination} />
        <Row label="Named service" value={decision.target?.service ?? 'none'} />
        <Row label="First call" value={decision.target?.entrypoint ?? 'none'} />
      </dl>

      <p className="text-xs leading-relaxed text-foreground/80">{decision.reason}</p>
    </section>
  )
}

export interface MlInsightPanelProps {
  status: MlStatusRead
  className?: string
}

/**
 * The panel body. Split from the query shell so the page owns the panel's
 * skeleton and error state and this file only renders an answer.
 */
export function MlInsightPanel({ status, className }: MlInsightPanelProps) {
  const [text, setText] = useState('')
  const [decision, setDecision] = useState<RoutingDecisionRead | null>(null)
  const queryClient = useQueryClient()

  const classify = useMutation({
    mutationFn: (utterance: string) => routeUtterance(utterance),
    onSuccess: (answer) => setDecision(answer),
    // A classification can warm a lazily loaded runtime, so the status read is
    // asked again rather than assumed to be unchanged.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: commandCenterKeys.mlStatus() })
    },
  })

  const trimmed = text.trim()

  /**
   * Why `Classify` is unavailable, or `null` when it is.
   *
   * A greyed-out button that does not say why is a dead end, and on a page whose
   * every other control is a link to somewhere real it is the only control that
   * cannot be reasoned about. The reason is the next sentence, not a tooltip
   * that only a mouse user can reach.
   */
  const classifyBlocked = classify.isPending
    ? 'Waiting for the classifier to answer. It is a cold model on first call, so this can take a while.'
    : !status.available
      ? 'The runtime is not serving classifications, so there is nothing to ask. Everything above is still read from GET /ml/status.'
      : trimmed.length === 0
        ? 'Type a sentence above; nothing is sent to the model until you do.'
        : null

  return (
    <div className={cn('space-y-4', className)}>
      <p className="flex flex-wrap items-center gap-2 text-xs leading-relaxed text-muted-foreground">
        {status.available ? (
          <Badge variant="success" className="gap-1 font-normal">
            <BrainCircuit aria-hidden="true" className="size-3" />
            Loaded
          </Badge>
        ) : (
          <Badge variant="warning" className="gap-1 font-normal">
            <CircleOff aria-hidden="true" className="size-3" />
            {status.enabled ? 'Not serving' : 'Disabled'}
          </Badge>
        )}
        <span>
          NEXUS runs one model: a fourteen-class intent classifier. It names the part of NEXUS
          one sentence was about. It is not a language model and it scores nothing here.
        </span>
      </p>

      {!status.available && (
        <p className="rounded-md border border-warning/40 bg-warning/[0.06] px-3 py-2 text-xs leading-relaxed text-foreground/85">
          The runtime is not serving classifications
          {status.unavailable_reason ? ` — the backend reported “${status.unavailable_reason}”` : ''}
          . Everything below is read from <code className="font-mono">GET /ml/status</code>, so it
          describes the deployment rather than anything you did.
        </p>
      )}

      {status.model ? (
        /*
         * Behind a disclosure, not in the open.
         *
         * Eight rows of model chrome — architecture, parameter count, sequence
         * length, the host's checkpoint path — were the tallest thing on the
         * page and the first thing a new account was shown, above a queue that
         * was empty. The identity that matters (`microsoft/deberta-v3-base`) is
         * named in the summary, so nothing is lost by not opening it, and the
         * rows are still one click away for anyone who is asking.
         */
        <details className="rounded-md border border-border px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-foreground">
            Model identity — {status.model.base_model}, loaded in{' '}
            {seconds(status.model.load_seconds)}
          </summary>
          <dl className="mt-2 divide-y divide-border/60">
            <Row label="Base model" value={status.model.base_model} />
            <Row label="Architecture" value={status.model.architecture} />
            <Row label="Device" value={status.model.device} />
            <Row label="Labels" value={String(status.model.label_count)} />
            <Row label="Parameters" value={formatNumber(status.model.parameter_count)} />
            <Row label="Max sequence length" value={String(status.model.max_sequence_length)} />
            <Row label="Load time" value={seconds(status.model.load_seconds)} />
            <Row label="Checkpoint" value={checkpointName(status.model.checkpoint)} />
          </dl>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            Read from <code className="font-mono">GET /ml/status</code>. The checkpoint is named
            by its artifact folder only; where the backend keeps it on its own disk is not
            something this account can act on.
          </p>
        </details>
      ) : (
        <p className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          No checkpoint is loaded, so there is no model identity to report. This is the
          backend's own state, not a figure this page worked out.
        </p>
      )}

      <div className="space-y-2 rounded-md border border-border p-3">
        <Label htmlFor="command-center-classify" className="text-xs">
          Classify one sentence
        </Label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            id="command-center-classify"
            value={text}
            placeholder="add a task called draft the report"
            aria-describedby="command-center-classify-blocked"
            onChange={(event) => setText(event.target.value)}
          />
          {/*
            The button is disabled on an empty box and says why, in the open.
            `disabled:pointer-events-none` means a `title` on the button itself
            never reaches the pointer, so the reason is a sentence under the
            field — readable by everyone, including a screen reader — and the
            title rides on the wrapper.
          */}
          <span className="inline-flex shrink-0" title={classifyBlocked ?? 'Classify this one sentence'}>
            <Button
              type="button"
              size="sm"
              aria-describedby="command-center-classify-blocked"
              disabled={classifyBlocked !== null}
              onClick={() => classify.mutate(trimmed)}
            >
              <Route aria-hidden="true" />
              {classify.isPending ? 'Classifying…' : 'Classify'}
            </Button>
          </span>
        </div>
        {classifyBlocked && (
          <p id="command-center-classify-blocked" className="text-xs leading-relaxed text-muted-foreground">
            {classifyBlocked}
          </p>
        )}
        <p className="text-xs leading-relaxed text-muted-foreground">
          One utterance in, one intent out. There is no history and no second model: the
          classifier cannot hold a conversation, extract an argument or perform the action.
        </p>
        {classify.isError && (
          <p role="alert" className="text-xs leading-relaxed text-destructive">
            The classifier did not answer. Nothing was inferred and nothing was changed — try
            again.
          </p>
        )}
      </div>

      {decision && <Prediction decision={decision} />}

      {status.intents.length > 0 && (
        <details className="rounded-md border border-border px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-foreground">
            The {status.intents.length} class{status.intents.length === 1 ? '' : 'es'} in taxonomy{' '}
            {status.taxonomy_version}
          </summary>
          <ul className="mt-2 space-y-1.5">
            {status.intents.map((route) => (
              <li key={route.intent} className="flex flex-wrap items-baseline gap-x-2 text-xs">
                <span className="font-mono text-foreground">{route.intent}</span>
                <span className="text-muted-foreground">→ {route.destination}</span>
                {route.service && (
                  <span className="font-mono text-muted-foreground">({route.service})</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
