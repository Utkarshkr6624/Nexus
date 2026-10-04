/**
 * A thin wrapper around the browser's own `SpeechRecognition` implementation.
 *
 * There is no React here on purpose. Recognition has a lifecycle that outlives
 * any render — the microphone is open, the browser owns the session — so the
 * useful shape is an imperative handle that reports through callbacks. The
 * assistant's hook owns the state; this file owns nothing.
 *
 * ## Privacy
 *
 * In Chrome and Edge, `SpeechRecognition` does not transcribe on the user's
 * machine. It streams the microphone audio to the browser vendor's own servers
 * (Google for Chrome, Microsoft for Edge) and transcribes there. **Using this
 * API means the user's voice leaves their device.** Firefox implements it not at
 * all, so there is no way to have voice input and a purely local guarantee at
 * the same time.
 *
 * NEXO accepts that trade because the alternative is forbidden, not merely
 * inconvenient: transcribing locally means shipping a speech model, and NEXO has
 * exactly one model — `microsoft/deberta-v3-base`, a text intent classifier. A
 * second model is a second claim on memory, on training pipeline and on trust
 * that this project has declined to make. The browser's engine is neither ours
 * nor a competitor's, and the disclosure is surfaced in the UI rather than left
 * in a source file. {@link RECOGNITION_PRIVACY_NOTICE} is that disclosure, in
 * the words the UI shows.
 */

import { RECOGNITION_TIMEOUT_MS } from './types'
import type { VoiceError } from './types'

/**
 * What the UI must tell the user before they turn the microphone on.
 *
 * Kept next to the code that can violate it, so the two cannot drift apart.
 */
export const RECOGNITION_PRIVACY_NOTICE =
  'Your browser transcribes your voice on its own servers, so the audio leaves this device. ' +
  'Chrome and Edge send it to Google and Microsoft respectively; Firefox has no speech recognition at all. ' +
  'NEXO uses the browser engine rather than a local model because NEXO runs no speech model of its own.'

/** Browser-supplied codes that mean something to the user. */
type SurfacedErrorCode = Exclude<SpeechRecognitionErrorCode, 'aborted'>

/**
 * Every surfaced browser code, translated.
 *
 * `aborted` is excluded from the key type rather than given an entry: it is what
 * the API reports when *we* stop a session, so surfacing it would turn every
 * deliberate stop into a failure. Excluding it at the type level means routing it
 * to `onError` is a compile error rather than a runtime mistake.
 *
 * `retryable` is a judgement about whether the same action could plausibly
 * succeed unchanged. A denied permission is worth retrying once the user has
 * changed a setting; a language this browser will never support is not.
 */
const RECOGNITION_ERRORS: Readonly<Record<SurfacedErrorCode, VoiceError>> = {
  'not-allowed': {
    code: 'permission_denied',
    message: 'Microphone access is blocked for this site. Allow it in your browser settings, then try again.',
    retryable: true,
  },
  'service-not-allowed': {
    code: 'permission_denied',
    message:
      'Your browser is blocking its own speech recognition service. Check that speech recognition is enabled, then try again.',
    retryable: true,
  },
  'audio-capture': {
    code: 'microphone_unavailable',
    message: 'No microphone was available. Connect one and try again.',
    retryable: true,
  },
  'no-speech': {
    code: 'no_speech',
    message: "I didn't catch any speech. Try again a little closer to your microphone.",
    retryable: true,
  },
  network: {
    code: 'recognition_failed',
    message:
      'Speech recognition needs a network connection and the service could not be reached. Check your connection and try again.',
    retryable: true,
  },
  'language-not-supported': {
    code: 'not_supported',
    message: "This browser cannot recognise speech in the selected language.",
    retryable: false,
  },
}

const NOT_SUPPORTED: VoiceError = {
  code: 'not_supported',
  message: "This browser has no speech recognition. Try Chrome or Edge, or type your request instead.",
  retryable: false,
}

const TIMED_OUT: VoiceError = {
  code: 'timeout',
  message: "I stopped listening without hearing a full request. Try again, and speak a little faster.",
  retryable: true,
}

/** How the recogniser reports back. Every callback is optional. */
export interface SpeechRecogniserOptions {
  /** BCP-47 tag. Omitted leaves the browser's own default in place. */
  lang?: string
  /** Partial results, for live feedback. Defaults to true. */
  interimResults?: boolean
  /** A partial hypothesis. Fires for live display only; never send it onward. */
  onInterim?: (text: string) => void
  /**
   * One complete utterance, trimmed. Fires at most once per session.
   *
   * This is the only text that may reach the classifier: NEXO's model is a
   * single-utterance intent classifier, so an interim fragment is not a worse
   * version of the request, it is not a request at all.
   */
  onFinal?: (transcript: string) => void
  /** A recognised failure. Never called for an `aborted` session. */
  onError?: (error: VoiceError) => void
  /** The session stopped, for any reason, successfully or not. */
  onEnd?: () => void
}

/** The handle `createSpeechRecogniser` returns. */
export interface SpeechRecogniser {
  /** Begin listening. A no-op if a session is already running. */
  start: () => void
  /** Stop and keep whatever was heard, so the browser can flush a final result. */
  stop: () => void
  /** Stop and discard, for cancelling without acting on a partial utterance. */
  abort: () => void
}

type RecognitionConstructor = new () => SpeechRecognition

interface RecognitionGlobals {
  SpeechRecognition?: unknown
  webkitSpeechRecognition?: unknown
}

/**
 * Safari ships only `webkitSpeechRecognition`; Chrome and Edge ship the spec
 * name. Reading through an explicit shape keeps the ambient declaration out of
 * the value position — the global is a constructor at runtime but never
 * something we can assume exists.
 */
function recognizerConstructor(): RecognitionConstructor | null {
  const scope = globalThis as unknown as RecognitionGlobals
  const candidate = scope.SpeechRecognition ?? scope.webkitSpeechRecognition
  return typeof candidate === 'function' ? (candidate as RecognitionConstructor) : null
}

/**
 * Whether this browser can listen at all.
 *
 * Safe to call anywhere, including where neither spelling exists: it reads two
 * properties and never touches a constructor. Callers resolve `VoiceState`'s
 * terminal `unsupported` from this at mount, so a `false` here is a state the
 * UI renders rather than an error it catches.
 */
export function isSpeechRecognitionSupported(): boolean {
  return recognizerConstructor() !== null
}

/** One result hypothesis, read defensively because a browser may send anything. */
function transcriptOf(result: SpeechRecognitionResult | undefined): string {
  const alternative = result?.[0]
  return typeof alternative?.transcript === 'string' ? alternative.transcript : ''
}

/**
 * Create a recogniser bound to a set of callbacks.
 *
 * The returned object holds no assistant state — it owns one browser session
 * and reports what happened. Callbacks fire from the browser's own event
 * handlers, so they are event handlers as far as React is concerned and safe to
 * set state from.
 *
 * Guarantees, because the consumer's state machine depends on them:
 *
 * - `onEnd` fires exactly once per session, whenever it started — after a final
 *   result, after an error, after a timeout, or after a bare stop.
 * - `onFinal` fires at most once per session, and never alongside an `onError`.
 * - An utterance that trims to nothing is reported as `no_speech`, not as a
 *   successful empty transcript: an empty string sent to the classifier is a
 *   request with no content, and 422 would be a worse description of it than
 *   "I didn't hear anything".
 */
export function createSpeechRecogniser(options: SpeechRecogniserOptions = {}): SpeechRecogniser {
  let session: SpeechRecognition | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let running = false

  function clearTimer(): void {
    if (timer === null) return
    clearTimeout(timer)
    timer = null
  }

  function report(error: VoiceError): void {
    options.onError?.(error)
  }

  function stop(): void {
    session?.stop()
  }

  function abort(): void {
    session?.abort()
  }

  function start(): void {
    // The browser throws `InvalidStateError` if a session is already running, and
    // the assistant's start button is reachable by keyboard and by pointer
    // within milliseconds of itself. Ignoring the second press is the honest
    // outcome: nothing went wrong, the assistant is already listening.
    if (running) return

    const Constructor = recognizerConstructor()
    if (Constructor === null) {
      report(NOT_SUPPORTED)
      options.onEnd?.()
      return
    }

    // A fresh instance per session. A recogniser that has ended may legally be
    // started again, but it carries the previous session's handlers and cannot
    // be restarted while its `onend` is still pending — which is exactly the
    // window a fast retry lands in.
    const recognition = new Constructor()
    session = recognition
    running = true
    // One `end` per session is guaranteed by the spec. The wrapper still latches
    // it, because the consumer's state machine is written against "onEnd fires
    // exactly once" and a browser that breaks the guarantee should degrade into
    // one redundant call rather than a state reset mid-turn.
    let ended = false

    if (options.lang !== undefined) recognition.lang = options.lang
    // One utterance, because the model behind this is single-utterance too.
    recognition.continuous = false
    recognition.maxAlternatives = 1
    recognition.interimResults = options.interimResults ?? true

    recognition.onresult = (event: SpeechRecognitionEvent): void => {
      let sawFinal = false
      let lastFinal = ''
      let interimText = ''

      // `resultIndex` is the first changed result. A browser can hand us an index
      // past the end of the list — a session torn down mid-dispatch — so the
      // bounds are checked rather than trusted.
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index]
        if (result === undefined) continue
        if (result.isFinal) {
          sawFinal = true
          // The *last* final, not the concatenation: several finals in one
          // dispatch are competing hypotheses for one utterance, and joining
          // them would produce a phrase the user never said.
          lastFinal = transcriptOf(result)
        } else {
          interimText += transcriptOf(result)
        }
      }

      if (sawFinal) {
        const trimmedFinal = lastFinal.trim()
        // An empty final is silence dressed as an utterance. Some browsers emit
        // it as an ordinary result rather than as a `no-speech` error, so the
        // outcome is derived rather than read off the event.
        if (trimmedFinal !== '') options.onFinal?.(trimmedFinal)
        else report(RECOGNITION_ERRORS['no-speech'])
        return
      }

      const trimmedInterim = interimText.trim()
      if (trimmedInterim !== '') options.onInterim?.(trimmedInterim)
    }

    recognition.onerror = (event: SpeechRecognitionErrorEvent): void => {
      clearTimer()
      // What we asked for. Reporting it would show a failure for the user's own
      // cancel button, and `onend` follows immediately anyway.
      if (event.error === 'aborted') return
      report(RECOGNITION_ERRORS[event.error])
    }

    recognition.onend = (): void => {
      if (ended) return
      ended = true
      clearTimer()
      running = false
      session = null
      options.onEnd?.()
    }

    // A recogniser left running never returns on its own: it holds the
    // microphone open indefinitely and leaves the user in a listening state
    // with no obvious way out of it. The deadline turns an unbounded session
    // into a reported `timeout`, and the abort releases the hardware.
    timer = setTimeout(() => {
      timer = null
      if (!running) return
      abort()
      report(TIMED_OUT)
    }, RECOGNITION_TIMEOUT_MS)

    recognition.start()
  }

  return { start, stop, abort }
}