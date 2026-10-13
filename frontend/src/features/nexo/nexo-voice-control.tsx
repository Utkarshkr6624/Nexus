import { Mic, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  RECOGNITION_PRIVACY_NOTICE,
  createSpeechRecogniser,
  isSpeechRecognitionSupported,
} from '@/features/assistant/speech-recognition'
import type { SpeechRecogniser } from '@/features/assistant/speech-recognition'
import type { VoiceError } from '@/features/assistant/types'

/**
 * "Talk to NEXO": one user-started recognition session per press.
 *
 * Reuses the AI Assistant's browser recogniser wrapper rather than a second voice stack.
 * With `autoSend` (the default on the NEXO page) the final transcript is sent as soon as the
 * utterance ends; otherwise it lands in the message box for review. Either way it goes down
 * exactly the same path as typed text — the same authorization, tool validation and crisis
 * guard. Nothing records until the button is pressed, and one session ends after one utterance.
 */

export type NexoVoiceStatus =
  | 'ready'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'permission_denied'
  | 'unsupported'
  | 'microphone_unavailable'
  | 'model_unavailable'
  | 'recognition_unavailable'
  | 'error'

const NEXO_VOICE_LABELS: Record<NexoVoiceStatus, string> = {
  ready: 'Ready',
  listening: 'Listening…',
  processing: 'Waiting for NEXO…',
  speaking: 'Speaking…',
  permission_denied: 'Microphone permission denied',
  unsupported: 'Voice not supported in this browser',
  microphone_unavailable: 'Microphone unavailable',
  model_unavailable: 'Off — turn the NEXO model on first',
  recognition_unavailable: 'Speech recognition unavailable',
  error: 'Voice error',
}

const PERMISSION_HELP =
  'To allow it: click the icon left of the address bar, open Site settings, set Microphone to Allow, then reload this page.'

export interface NexoVoiceControlProps {
  /** Called with the final transcript; the page sends it, or puts it in the message box. */
  onTranscript: (text: string) => void
  /** The transcript is sent when the utterance ends (shown so the user knows). */
  autoSend?: boolean
  /** The model server is reachable, so a sent request can be answered. */
  modelReady: boolean
  /** A NEXO request is in flight. */
  busy: boolean
  /** A reply is being read aloud. */
  speaking: boolean
}

function statusOf(
  supported: boolean,
  listening: boolean,
  error: VoiceError | null,
  { modelReady, busy, speaking }: Pick<NexoVoiceControlProps, 'modelReady' | 'busy' | 'speaking'>,
): NexoVoiceStatus {
  if (!supported) return 'unsupported'
  if (listening) return 'listening'
  if (busy) return 'processing'
  if (speaking) return 'speaking'
  if (error) {
    if (error.code === 'permission_denied') return 'permission_denied'
    if (error.code === 'microphone_unavailable') return 'microphone_unavailable'
    if (error.code === 'recognition_failed' || error.code === 'not_supported') {
      return 'recognition_unavailable'
    }
    return 'error'
  }
  if (!modelReady) return 'model_unavailable'
  return 'ready'
}

export function NexoVoiceControl({
  onTranscript,
  modelReady,
  busy,
  speaking,
  autoSend = false,
}: NexoVoiceControlProps) {
  const [supported] = useState(isSpeechRecognitionSupported)
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<VoiceError | null>(null)
  const recogniser = useRef<SpeechRecogniser | null>(null)
  const deliver = useRef(onTranscript)

  useEffect(() => {
    deliver.current = onTranscript
  }, [onTranscript])
  useEffect(() => () => recogniser.current?.abort(), [])

  const status = statusOf(supported, listening, error, { modelReady, busy, speaking })

  function start() {
    setError(null)
    setInterim('')
    recogniser.current ??= createSpeechRecogniser({
      onInterim: setInterim,
      onFinal: (text) => deliver.current(text),
      onError: setError,
      onEnd: () => {
        setListening(false)
        setInterim('')
      },
    })
    setListening(true)
    recogniser.current.start()
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        {listening ? (
          <Button type="button" variant="destructive" onClick={() => recogniser.current?.stop()}>
            <Square aria-hidden="true" />
            Stop listening
          </Button>
        ) : (
          <Button
            type="button"
            variant="outline"
            onClick={start}
            disabled={!supported || !modelReady || busy}
          >
            <Mic aria-hidden="true" />
            Talk to NEXO
          </Button>
        )}
        <span role="status" aria-live="polite" className="text-xs text-muted-foreground">
          Voice: {NEXO_VOICE_LABELS[status]}
          {interim && ` — transcribing “${interim}”`}
          {status === 'listening' && autoSend && ' (sent when you stop speaking)'}
        </span>
      </div>
      {status === 'unsupported' && (
        <p className="text-xs text-muted-foreground">
          This browser has no speech recognition (Chrome and Edge do). Type your message instead.
        </p>
      )}
      {error && status !== 'unsupported' && (
        <p className="text-xs text-destructive">
          {error.message} {error.code === 'permission_denied' && PERMISSION_HELP}
        </p>
      )}
      {supported && <p className="text-xs text-muted-foreground">{RECOGNITION_PRIVACY_NOTICE}</p>}
    </div>
  )
}
