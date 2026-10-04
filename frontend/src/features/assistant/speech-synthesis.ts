/**
 * A thin wrapper around `window.speechSynthesis`.
 *
 * The companion to `speech-recognition.ts` and, unlike it, entirely local:
 * synthesis is done by the operating system's voice, so nothing is uploaded.
 *
 * NEXO's classifier cannot answer in prose, so what is spoken here is always a
 * short, already-decided string — the route a request landed on, or the fact
 * that it did not. That bounds the awkward cases: utterances are a sentence or
 * two, never a paragraph to read out.
 *
 * No React, for the same reason as recognition: the audio queue outlives any
 * render and the handle reports through callbacks.
 */

import type { VoiceError } from './types'

const SYNTHESIS_FAILED: VoiceError = {
  code: 'synthesis_failed',
  message: 'This browser could not read that reply aloud.',
  retryable: true,
}

/**
 * Browser codes translated. None of them is ever shown to the user — the strings
 * are implementation vocabulary, and "synthesis-unavailable" in an error banner
 * tells someone nothing they can act on.
 */
const SYNTHESIS_ERRORS: Readonly<Record<SpeechSynthesisErrorCode, VoiceError>> = {
  // Reached only if the browser cancels something we did not cancel ourselves.
  canceled: {
    code: 'synthesis_failed',
    message: 'The spoken reply stopped before it finished.',
    retryable: true,
  },
  interrupted: {
    code: 'synthesis_failed',
    message: 'The spoken reply was cut off.',
    retryable: true,
  },
  'audio-busy': {
    code: 'synthesis_failed',
    message: 'The audio device is busy with another sound.',
    retryable: true,
  },
  'audio-hardware': {
    code: 'synthesis_failed',
    message: 'This browser could not reach the audio output.',
    retryable: true,
  },
  'invalid-argument': {
    code: 'synthesis_failed',
    message: 'This browser would not speak that reply.',
    retryable: false,
  },
  'language-unavailable': {
    code: 'synthesis_failed',
    message: 'This browser has no voice for the selected language.',
    retryable: false,
  },
  network: {
    code: 'synthesis_failed',
    message: 'Spoken output needs a network connection and it could not be reached.',
    retryable: true,
  },
  'not-allowed': {
    code: 'synthesis_failed',
    message: 'Your browser is blocking spoken replies.',
    retryable: false,
  },
  'synthesis-failed': {
    code: 'synthesis_failed',
    message: 'This browser could not read that reply aloud.',
    retryable: true,
  },
  'synthesis-unavailable': {
    code: 'synthesis_failed',
    message: 'This browser has no spoken output.',
    retryable: false,
  },
  'text-too-long': {
    code: 'synthesis_failed',
    message: 'That reply is too long to read aloud.',
    retryable: false,
  },
  'voice-unavailable': {
    code: 'synthesis_failed',
    message: 'This browser has no voice for the selected language.',
    retryable: false,
  },
}

const NOT_SUPPORTED: VoiceError = {
  code: 'synthesis_failed',
  message: 'This browser cannot speak, so replies will be shown as text only.',
  retryable: false,
}

export interface SpeakOptions {
  /** Fires when audio actually begins. */
  onStart?: () => void
  /** Fires exactly once per started utterance, however it stopped. */
  onEnd?: () => void
  /** A recognised failure. */
  onError?: (error: VoiceError) => void
  /** Speaking rate. Defaults to 1. */
  rate?: number
  /** Voice pitch. Defaults to 1. */
  pitch?: number
  /** An explicit voice. Wins over `lang`. */
  voice?: SpeechSynthesisVoice
  /** Preferred language tag, e.g. `en-GB`. A matching voice is used if present. */
  lang?: string
}

/** What a cancelled or replaced utterance left behind, so it reports only once. */
interface ActiveUtterance {
  settle: () => void
}

let active: ActiveUtterance | null = null

/**
 * Whether this browser can speak.
 *
 * Safe to call anywhere: it reads a property and never constructs anything. The
 * assistant hides its spoken-reply affordance rather than offering a button that
 * cannot work.
 */
export function isSpeechSynthesisSupported(): boolean {
  const synth = globalThis.speechSynthesis
  return (
    typeof synth === 'object' &&
    synth !== null &&
    typeof synth.speak === 'function' &&
    typeof synth.cancel === 'function'
  )
}

function synthesis(): SpeechSynthesis | null {
  return isSpeechSynthesisSupported() ? globalThis.speechSynthesis : null
}

/**
 * Best available voice for a language tag.
 *
 * Voice lists load asynchronously in some browsers, so an empty list is a normal
 * state rather than a failure. A missing match falls through to the browser's
 * own default, which is the right answer: a slightly wrong accent is better
 * than no spoken output at all.
 */
function pickVoice(lang: string | undefined): SpeechSynthesisVoice | null {
  const synth = synthesis()
  if (synth === null || lang === undefined) return null
  let voices: SpeechSynthesisVoice[]
  try {
    voices = synth.getVoices()
  } catch {
    return null
  }
  const wanted = lang.toLowerCase()
  const base = wanted.split('-')[0] ?? wanted
  return (
    voices.find((voice) => voice.lang.toLowerCase() === wanted) ??
    voices.find((voice) => voice.lang.toLowerCase().split('-')[0] === base) ??
    null
  )
}

/**
 * Stop whatever is speaking and let its caller know.
 *
 * `onEnd` is settled here rather than left to the browser's `end` event, because
 * the browser will also fire one and the consumer must see exactly one. Settling
 * first makes the count independent of which of the two arrives.
 */
function cancelActive(): void {
  const current = active
  // Only reach the browser when there is something of ours to stop. Every
  // utterance NEXO creates is tracked here, so an unconditional `cancel()` would
  // only be clearing a queue we had already emptied — and it would report a stop
  // for a line that had not started.
  if (current === null) return
  active = null
  synthesis()?.cancel()
  current.settle()
}

/**
 * Speak a line, replacing anything already being said.
 *
 * Returns whether audio actually started. `false` means nothing was spoken and
 * **no callback will fire** — blank text, or a browser with no synthesis. Callers
 * that drive a `speaking` state must therefore branch on the return value rather
 * than waiting for `onEnd`.
 *
 * Whitespace-only text is refused outright. An empty utterance is a known way to
 * wedge a synthesis queue: the browser may accept it, report no error, and never
 * emit `end`, leaving the assistant permanently "speaking". There is nothing to
 * say, so there is nothing to risk.
 *
 * Calling this while speech is in progress cancels the previous utterance first.
 * Without that, a user who asks a second question during the first reply hears
 * both answers at once and cannot tell which belongs to which — the assistant
 * would be talking over its own answer. The cancelled utterance's `onEnd` is
 * settled before the replacement begins, so callbacks stay ordered:
 * `onEnd` (old), then `onStart` (new).
 */
export function speak(text: string, options: SpeakOptions = {}): boolean {
  if (text.trim() === '') return false

  const synth = synthesis()
  if (synth === null) {
    options.onError?.(NOT_SUPPORTED)
    return false
  }

  cancelActive()

  const utterance = new SpeechSynthesisUtterance(text)
  utterance.rate = options.rate ?? 1
  utterance.pitch = options.pitch ?? 1
  const voice = options.voice ?? pickVoice(options.lang)
  if (voice !== null) utterance.voice = voice

  let settled = false
  const settle = (): void => {
    if (settled) return
    settled = true
    if (active !== null && active.settle === settle) active = null
    options.onEnd?.()
  }

  utterance.onstart = (): void => options.onStart?.()
  utterance.onend = settle
  utterance.onerror = (event: SpeechSynthesisErrorEvent): void => {
    // A browser that cancels or interrupts on our own `cancel()` has told us
    // nothing we do not already know, and reporting it would raise an error for
    // a deliberate action.
    if (event.error === 'canceled' || event.error === 'interrupted') {
      settle()
      return
    }
    if (settled) return
    settled = true
    active = null
    options.onError?.(SYNTHESIS_ERRORS[event.error] ?? SYNTHESIS_FAILED)
    options.onEnd?.()
  }

  active = { settle }

  try {
    synth.speak(utterance)
  } catch {
    // Some browsers throw rather than reporting, notably when the queue is in a
    // state it dislikes. The caller must hear about it either way.
    settle()
    options.onError?.(SYNTHESIS_FAILED)
    return false
  }

  return true
}

/**
 * Cancel the current utterance, if any.
 *
 * The utterance's `onEnd` is settled here, so a caller leaving a `speaking`
 * state does not depend on which browsers fire `end` after a cancel.
 */
export function cancel(): void {
  cancelActive()
}