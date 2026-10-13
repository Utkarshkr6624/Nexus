import { useQuery } from '@tanstack/react-query'
import { Bot, CircleAlert, CircleCheck, Power, ShieldCheck, Volume2, VolumeX } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { PageHeader } from '@/components/feedback/page-header'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import {
  cancel as cancelSpeech,
  isSpeechSynthesisSupported,
  speak,
} from '@/features/assistant/speech-synthesis'
import { NexoVoiceControl } from '@/features/nexo/nexo-voice-control'
import type { ApiError } from '@/lib/api-client'
import { toApiError } from '@/services/errors'
import {
  confirmJarvisAction,
  fetchJarvisStatus,
  sendJarvisChat,
  setJarvisModel,
} from '@/services/jarvis'
import type {
  JarvisLifecycle,
  JarvisMessage,
  JarvisModelState,
  JarvisStatusRead,
  JarvisToolStatus,
  JarvisTurnRead,
} from '@/services/jarvis'

/**
 * NEXO (internally "Jarvis"), the Phase 7 generative assistant: Qwen3-8B with the
 * checkpoint_v31_139 adapter. It is a separate system from the AI Assistant at /assistant,
 * which is NEXUS's microsoft/deberta-v3-base intent classifier.
 *
 * The page never decides anything itself. It shows the server's own status (is a model
 * reachable, which tools are blocked and why), sends the conversation, and renders each
 * turn's outcome, tool trace and guard result exactly as returned. Side effects wait for an
 * explicit Approve; the server runs only the proposal it stored, once, for the same user.
 */

const MAX_CONTENT = 8000

/** What each side-effect tool does when approved; read-only tools never reach a confirmation card. */
const TOOL_EFFECTS: Record<string, string> = {
  fs_write_file: 'Create or overwrite a file in the NEXO workspace folder.',
  fs_move_file: 'Move or rename a file inside the NEXO workspace folder.',
  fs_delete_file: 'Permanently delete file(s) in the NEXO workspace folder.',
  http_request: 'Send an HTTP request to a public internet address (it may change data there).',
  web_fetch:
    'Fetch a public web page after private data was read in this turn (it could carry that data off this machine).',
  web_search:
    'Send a search query to the configured search provider after private data was read in this turn.',
  shell_run: 'Run an allowlisted program inside the workspace folder.',
  app_create_task: 'Create a task in your NEXUS projects.',
  app_update_task: 'Change one of your NEXUS tasks.',
  app_create_event: 'Add an event to your NEXUS calendar.',
  app_update_event: 'Change an event in your NEXUS calendar.',
  app_update_goal: 'Change the progress of one of your learning goals.',
  workspace_create_file: 'Create a new file in the NEXO workspace folder (never overwrites).',
  workspace_backup_file: 'Copy a workspace file into .nexo_backups/ and verify the copy.',
  code_apply_patch: 'Change workspace files by applying this patch.',
  run_project_tests: 'Run the approved project test command in the workspace folder.',
  run_project_lint: 'Run the approved project lint command in the workspace folder.',
}

const APPROVAL_REASON: Record<string, string> = {
  write: 'It changes your data.',
  destructive_write: 'It deletes data and cannot be undone.',
  external_write: 'It reaches a system outside NEXUS and may change something there.',
  execute: 'It runs a program on this machine.',
  read: 'It would send data read earlier in this turn to an outside service.',
}

const TARGET_KEYS = [
  'path',
  'source',
  'destination',
  'url',
  'method',
  'task',
  'event',
  'goal',
  'title',
  'command',
  'path_a',
  'path_b',
]

type Decision = 'pending' | 'approved' | 'declined' | 'expired' | 'unknown'

interface SearchHit {
  title: string
  url: string
  snippet?: string
}

/** The real rows a successful web_search returned, so the user can see the sources and not only the model's words. */
function SearchSources({ data }: { data: unknown }) {
  const d = data as { coverage?: string; results?: SearchHit[] } | null
  const hits = (d?.results ?? []).filter((h) => /^https?:\/\//.test(String(h?.url)))
  return (
    <div className="space-y-0.5">
      <p>
        Search results ({d?.coverage ?? 'configured provider'}):{' '}
        {hits.length === 0 && 'no results.'}
      </p>
      {hits.length > 0 && (
        <ul className="list-disc pl-4">
          {hits.map((h, i) => (
            <li key={i}>
              <a href={h.url} target="_blank" rel="noopener noreferrer" className="underline">
                {h.title}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface Entry {
  role: 'user' | 'assistant' | 'error'
  content: string
  turn?: JarvisTurnRead
  decision?: Decision
}

function ToolRow({ t }: { t: JarvisToolStatus }) {
  return (
    <li>
      <code>{t.name}</code>{' '}
      {!t.available ? (
        <Badge variant="warning">unavailable</Badge>
      ) : t.requires_confirmation ? (
        <Badge variant="outline">available · asks before running</Badge>
      ) : (
        <Badge variant="success">available</Badge>
      )}
      {t.reason && <span className="text-muted-foreground"> — {t.reason}</span>}
    </li>
  )
}

const MODEL_STATE_TEXT: Record<JarvisModelState, string> = {
  off: 'NEXO model is off',
  loading: 'Loading the NEXO model…',
  ready: 'NEXO model is ready',
  busy: 'NEXO model is busy',
  unloading: 'Unloading the NEXO model…',
  error: 'NEXO model error',
  external: 'NEXO model is running (started outside NEXUS)',
}

function ModelSwitch({
  lifecycle,
  switching,
  onSwitch,
}: {
  lifecycle: JarvisLifecycle
  switching: boolean
  onSwitch: (on: boolean) => void
}) {
  const { state } = lifecycle
  const on = state === 'ready' || state === 'busy' || state === 'loading'
  return (
    <div className="space-y-1 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-3">
        <span role="status" aria-live="polite" className="font-medium">
          {MODEL_STATE_TEXT[state]}
        </span>
        {lifecycle.on_demand && state !== 'external' && (
          <Button
            type="button"
            size="sm"
            variant={on ? 'outline' : 'default'}
            disabled={switching || state === 'unloading'}
            onClick={() => onSwitch(!on)}
          >
            <Power aria-hidden="true" />
            {on ? 'Turn NEXO off' : 'Turn NEXO on'}
          </Button>
        )}
        {(state === 'loading' || state === 'unloading' || switching) && (
          <Spinner aria-hidden="true" />
        )}
      </div>
      {lifecycle.error && <p className="text-destructive">{lifecycle.error}</p>}
      {state === 'off' && (
        <p className="text-muted-foreground">
          Qwen3-8B is not using any memory. Turning NEXO on loads it (a few minutes); NEXUS and the AI
          Assistant work either way.
          {lifecycle.idle_unload_minutes > 0 &&
            ` It switches itself off after ${lifecycle.idle_unload_minutes} minutes without use.`}
        </p>
      )}
      {!lifecycle.on_demand && state !== 'external' && (
        <p className="text-muted-foreground">
          This server cannot load the model itself (start NEXUS with start.cmd to enable the switch).
        </p>
      )}
    </div>
  )
}

function StatusCard({
  status,
  loading,
  switching,
  onSwitch,
}: {
  status?: JarvisStatusRead
  loading: boolean
  switching: boolean
  onSwitch: (on: boolean) => void
}) {
  if (loading) return <Spinner aria-label="Checking NEXO status" />
  if (!status) return null
  const ready = status.enabled && status.model.available
  const unavailable = status.tools.filter((t) => !t.available)
  const asks = status.tools.filter((t) => t.available && t.requires_confirmation)
  const direct = status.tools.filter((t) => t.available && !t.requires_confirmation)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {ready ? (
            <CircleCheck className="size-4 text-success" aria-hidden="true" />
          ) : (
            <CircleAlert className="size-4 text-warning" aria-hidden="true" />
          )}
          {ready ? 'Model connected' : 'Model not available'}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {!status.enabled && (
          <p>NEXO is turned off on this server (JARVIS_ENABLED=false). NEXUS is unaffected.</p>
        )}
        {status.enabled && status.lifecycle && (
          <ModelSwitch lifecycle={status.lifecycle} switching={switching} onSwitch={onSwitch} />
        )}
        {status.enabled && !status.model.available && !status.lifecycle?.on_demand && (
          <p>
            The backend cannot reach the NEXO model server: {status.model.reason}. Chat requests
            will be refused until it is running.
          </p>
        )}
        {ready && (
          <p>
            {status.model.model_id} with adapter {status.model.adapter_sha256?.slice(0, 8)}…
          </p>
        )}
        <p className="text-muted-foreground">{status.qualification}</p>
        <details>
          <summary className="cursor-pointer">
            Tools: {direct.length} run directly, {asks.length} ask before running,{' '}
            {unavailable.length} unavailable
          </summary>
          <p className="mt-2 text-muted-foreground">
            Reading tools run as soon as NEXO calls them. A tool marked “asks before running” is
            available too: when NEXO wants to use it, nothing happens until you see the exact call
            and press Approve. Declining, or an approval that expires (10 minutes, or a server
            restart), runs nothing. Ordinary conversation never needs approval.
          </p>
          <ul className="mt-2 space-y-1">
            {[...direct, ...asks, ...unavailable].map((t) => (
              <ToolRow key={t.name} t={t} />
            ))}
          </ul>
        </details>
      </CardContent>
    </Card>
  )
}

function TurnDetails({ turn }: { turn: JarvisTurnRead }) {
  return (
    <div className="mt-2 space-y-1 text-xs text-muted-foreground">
      <div className="flex flex-wrap gap-1">
        <Badge variant="outline">{turn.outcome}</Badge>
        {turn.guards.crisis === 'replaced' && (
          <Badge variant="warning" className="gap-1">
            <ShieldCheck className="size-3" aria-hidden="true" />
            safety guard replaced the model's answer
          </Badge>
        )}
        {turn.guards.honesty === 'replaced' && (
          <Badge variant="warning">unsupported claim replaced</Badge>
        )}
      </div>
      {turn.trace.length > 0 && (
        <ul className="space-y-0.5">
          {turn.trace.map((t, i) => (
            <li key={i}>
              <code>{t.tool}</code> — {t.status}
              {t.result?.error && `: ${t.result.error.code} (${t.result.error.message})`}
            </li>
          ))}
        </ul>
      )}
      {turn.trace
        .filter((t) => t.tool === 'web_search' && t.result?.ok)
        .map((t, i) => (
          <SearchSources key={i} data={t.result?.data} />
        ))}
    </div>
  )
}

const DECISION_TEXT: Record<Exclude<Decision, 'pending'>, string> = {
  approved: 'Approved — the result is below.',
  declined: 'Declined — nothing was run.',
  expired: 'This approval expired, was already used, or the server restarted. Nothing was run.',
  unknown:
    'Approved, but no answer came back, so it is not known whether the action ran. Check before asking again; this approval cannot be reused.',
}

function ConfirmationCard({
  turn,
  tools,
  decision,
  busy,
  onDecide,
}: {
  turn: JarvisTurnRead
  tools: JarvisToolStatus[]
  decision: Decision
  busy: boolean
  onDecide: (approve: boolean) => void
}) {
  return (
    <div className="mt-2 space-y-2 rounded-md bg-muted p-2" aria-label="Approval request">
      <p className="text-xs font-medium">
        NEXO wants to run {turn.proposal?.length === 1 ? 'this action' : 'these actions'}:
      </p>
      {turn.proposal?.map((call, i) => {
        const side = tools.find((t) => t.name === call.name)?.side_effect ?? 'write'
        const targets = TARGET_KEYS.filter((k) => k in call.arguments)
        return (
          <div key={i} className="space-y-1 rounded border bg-background p-2 text-xs">
            <p>
              <code className="font-medium">{call.name}</code> —{' '}
              {TOOL_EFFECTS[call.name] ?? 'Run this tool.'}
            </p>
            {targets.length > 0 && (
              <p>
                Target:{' '}
                {targets.map((k) => (
                  <span key={k} className="mr-2">
                    {k} <code>{String(call.arguments[k])}</code>
                  </span>
                ))}
              </p>
            )}
            <p className="text-muted-foreground">
              Why approval: {APPROVAL_REASON[side] ?? APPROVAL_REASON.write}
            </p>
            {call.unverified && call.unverified.length > 0 && (
              <p role="alert" className="font-medium text-destructive">
                Check {call.unverified.join(', ')}: not in your words, NEXO may have changed it.
              </p>
            )}
            <pre className="overflow-x-auto">{JSON.stringify(call.arguments, null, 2)}</pre>
          </div>
        )
      })}
      {decision === 'pending' ? (
        <div className="flex gap-2">
          <Button size="sm" disabled={busy} onClick={() => onDecide(true)}>
            Approve
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide(false)}>
            Decline
          </Button>
        </div>
      ) : (
        <p className="text-xs font-medium">{DECISION_TEXT[decision]}</p>
      )}
    </div>
  )
}

export default function JarvisPage() {
  const status = useQuery({
    queryKey: ['jarvis', 'status'],
    queryFn: ({ signal }) => fetchJarvisStatus(signal),
    // Follow a load or unload until it settles; otherwise no polling.
    refetchInterval: (query) => {
      const state = query.state.data?.lifecycle?.state
      return state === 'loading' || state === 'unloading' ? 2000 : false
    },
  })
  const [entries, setEntries] = useState<Entry[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [switching, setSwitching] = useState(false)
  const [speakReplies, setSpeakRepliesState] = useState(false)
  // Read by readAloud after an await, so it must not be a stale render's value.
  const speakRef = useRef(false)
  const [autoSend, setAutoSend] = useState(true)
  const [speaking, setSpeaking] = useState(false)
  const [speechError, setSpeechError] = useState<string | null>(null)
  const inFlight = useRef(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const canSpeak = isSpeechSynthesisSupported()
  const modelReady = Boolean(status.data?.enabled && status.data.model.available)
  // Known to be off: sending would only come back as a 503, so say so up front instead.
  const modelOff = Boolean(status.data) && !modelReady
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => () => cancelSpeech(), [])
  // Keep the newest message (or the "working" line) in view as the conversation grows.
  // Block body on purpose: newer Chrome's scrollIntoView returns a Promise, and an effect must return nothing.
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'nearest' })
  }, [entries, busy])

  function setSpeakReplies(on: boolean) {
    speakRef.current = on
    setSpeakRepliesState(on)
  }

  async function switchModel(on: boolean) {
    setSwitching(true)
    try {
      await setJarvisModel(on)
    } catch (cause) {
      setEntries((list) => [...list, { role: 'error', content: toApiError(cause).message }])
    } finally {
      setSwitching(false)
      void status.refetch()
    }
  }

  const history = (list: Entry[]): JarvisMessage[] =>
    list
      .filter((e): e is Entry & { role: 'user' | 'assistant' } => e.role !== 'error')
      .map((e) => ({ role: e.role, content: e.content.slice(0, MAX_CONTENT) }))

  function readAloud(text: string) {
    if (!speakRef.current || !canSpeak) return
    setSpeechError(null)
    speak(text, {
      onStart: () => setSpeaking(true),
      onEnd: () => setSpeaking(false),
      onError: (error) => {
        setSpeaking(false)
        setSpeechError(error.message)
      },
    })
  }

  async function run(
    next: Entry[],
    call: () => Promise<JarvisTurnRead>,
    onFail?: (error: ApiError) => Entry[],
  ) {
    // `busy` is render state: two clicks (or Enter plus a voice transcript) in one tick both see `false`.
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setEntries(next)
    try {
      const turn = await call()
      setEntries([
        ...next,
        {
          role: 'assistant',
          content: turn.text,
          turn,
          decision: turn.proposal_id ? 'pending' : undefined,
        },
      ])
      readAloud(turn.text) // the guarded server text, never anything composed here
    } catch (cause) {
      const error = toApiError(cause)
      const base = onFail ? onFail(error) : next
      if (error.status === 404 && onFail) {
        setEntries(base)
      } else {
        setEntries([...base, { role: 'error', content: error.message }])
        void status.refetch()
      }
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  function submit(text: string) {
    if (!text || busy) return
    setDraft('')
    const next: Entry[] = [...entries, { role: 'user', content: text.slice(0, MAX_CONTENT) }]
    void run(next, () => sendJarvisChat(history(next)))
  }

  function send(event: FormEvent) {
    event.preventDefault()
    if (!modelOff) submit(draft.trim())
  }

  function decide(index: number, proposalId: string, approve: boolean) {
    const mark = (d: Decision) => entries.map((e, i) => (i === index ? { ...e, decision: d } : e))
    void run(
      mark(approve ? 'approved' : 'declined'),
      () => confirmJarvisAction(proposalId, approve),
      (error) =>
        mark(
          error.status === 404
            ? 'expired'
            : // No HTTP answer, or the server failed mid-request: it may have run, so claim neither.
              approve && (error.status === 0 || (error.status >= 500 && error.status !== 503))
              ? 'unknown'
              : // Refused before the proposal was taken (auth, 503 busy): it is still pending, so allow a retry.
                'pending',
        ),
    )
  }

  function onTranscript(text: string) {
    // Talking to NEXO means wanting to hear it back; the speaker button turns this off again.
    if (canSpeak) setSpeakReplies(true)
    if (autoSend) {
      // Same submit path as typed text: same request, authorization, tools and guards.
      submit(text.trim())
    } else {
      setDraft(text)
      input.current?.focus()
    }
  }

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        eyebrow="Platform"
        title="NEXO"
        description="A generative assistant that can use NEXUS tools, by text or voice. Anything with a side effect waits for your approval."
        badges={
          <>
            <Badge variant="outline" className="gap-1 font-normal">
              <Bot aria-hidden="true" />
              Qwen3-8B · checkpoint_v31_139
            </Badge>
            <Badge variant="warning" className="font-normal">
              Development candidate — not acceptance-qualified
            </Badge>
          </>
        }
      />

      <StatusCard
        status={status.data}
        loading={status.isLoading}
        switching={switching}
        onSwitch={(on) => void switchModel(on)}
      />
      {status.isError && (
        <Alert variant="destructive">
          <AlertTitle>Could not read NEXO status</AlertTitle>
          <AlertDescription>{toApiError(status.error).message}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardContent className="space-y-4 pt-6">
          <ol className="space-y-3" aria-label="Conversation" aria-live="polite">
            {entries.length === 0 && (
              <li className="text-sm text-muted-foreground">
                Ask NEXO something, e.g. “What tasks do I have open?”, or press Talk to NEXO.
              </li>
            )}
            {entries.map((e, i) => (
              <li
                key={i}
                className={
                  e.role === 'user'
                    ? 'ml-auto max-w-[85%] rounded-lg bg-primary/10 px-3 py-2 text-sm'
                    : e.role === 'error'
                      ? 'max-w-[85%] rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive'
                      : 'max-w-[85%] rounded-lg border px-3 py-2 text-sm'
                }
              >
                <span className="sr-only">
                  {e.role === 'user' ? 'You' : e.role === 'error' ? 'Error' : 'NEXO'}:{' '}
                </span>
                <p className="whitespace-pre-wrap">{e.content}</p>
                {e.turn && <TurnDetails turn={e.turn} />}
                {e.turn?.proposal_id && e.turn.proposal && e.decision && (
                  <ConfirmationCard
                    turn={e.turn}
                    tools={status.data?.tools ?? []}
                    decision={e.decision}
                    busy={busy}
                    onDecide={(approve) => decide(i, e.turn!.proposal_id!, approve)}
                  />
                )}
              </li>
            ))}
            {busy && (
              <li className="flex items-center gap-2 text-sm text-muted-foreground">
                <Spinner aria-hidden="true" /> NEXO is working…
              </li>
            )}
          </ol>

          <form onSubmit={send} className="flex gap-2">
            <label htmlFor="jarvis-input" className="sr-only">
              Message NEXO
            </label>
            <textarea
              id="jarvis-input"
              ref={input}
              rows={2}
              value={draft}
              maxLength={MAX_CONTENT}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                // Enter that confirms an IME composition (Chinese, Japanese, Korean input) is not a send.
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  send(event)
                }
              }}
              placeholder={modelOff ? 'Turn NEXO on above to start chatting' : 'Message NEXO'}
              className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
            <Button type="submit" disabled={busy || modelOff || !draft.trim()}>
              Send
            </Button>
          </form>
          <div ref={end} />

          <div className="flex flex-wrap items-start justify-between gap-3">
            <NexoVoiceControl
              onTranscript={onTranscript}
              modelReady={modelReady}
              busy={busy}
              speaking={speaking}
              autoSend={autoSend}
            />
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={autoSend}
                onChange={(event) => setAutoSend(event.target.checked)}
              />
              Send automatically when I stop speaking
            </label>
            {canSpeak ? (
              <div className="flex items-center gap-2 text-sm">
                <Button
                  type="button"
                  size="sm"
                  variant={speakReplies ? 'default' : 'outline'}
                  aria-pressed={speakReplies}
                  onClick={() => {
                    if (speakReplies) cancelSpeech()
                    setSpeakReplies(!speakReplies)
                  }}
                >
                  {speakReplies ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}
                  {speakReplies ? 'NEXO speaks replies: on' : 'NEXO speaks replies: off'}
                </Button>
                {speaking && (
                  <Button type="button" size="sm" variant="outline" onClick={() => cancelSpeech()}>
                    <VolumeX aria-hidden="true" />
                    Stop speaking
                  </Button>
                )}
                {speechError && (
                  <span role="status" className="text-xs text-destructive">
                    {speechError} The reply is still shown as text.
                  </span>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                This browser cannot speak replies aloud; they stay visible as text.
              </p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
