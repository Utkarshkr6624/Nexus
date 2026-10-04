/**
 * Ambient declarations for the Web Speech recognition API.
 *
 * TypeScript's `lib.dom.d.ts` (5.7.3) declares the whole `SpeechSynthesis`
 * family but **not** `SpeechRecognition` — only the result interfaces
 * (`SpeechRecognitionAlternative`, `SpeechRecognitionResult`,
 * `SpeechRecognitionResultList`). There is no `SpeechRecognition` interface, no
 * global constructor, and no `SpeechRecognitionEvent`. This file supplies them.
 *
 * Two browsers matter and neither matches the spec name exactly:
 *
 * - Chrome and Edge ship it as `SpeechRecognition` and require a network round
 *   trip — audio leaves the machine. See the privacy note in
 *   `src/features/assistant/speech-recognition.ts`.
 * - Safari ships only `webkitSpeechRecognition`.
 * - Firefox ships neither.
 *
 * So the constructor is declared on both spellings and feature detection is a
 * runtime concern, never a compile-time one. Nothing here widens a global the
 * browser does not actually provide; `speech-recognition.ts` narrows before use.
 */

/** Per-utterance result produced by the recogniser. */
interface SpeechRecognitionAlternative {
  readonly transcript: string
  readonly confidence: number
}

/** One recognition hypothesis for a phrase. */
interface SpeechRecognitionResult {
  readonly isFinal: boolean
  readonly length: number
  item(index: number): SpeechRecognitionAlternative
  [index: number]: SpeechRecognitionAlternative
}

/** All hypotheses for the utterance so far. */
interface SpeechRecognitionResultList {
  readonly length: number
  item(index: number): SpeechRecognitionResult
  [index: number]: SpeechRecognitionResult
}

/**
 * A single recognition event.
 *
 * Declared with an index signature so `event.results` can be indexed directly —
 * `resultIndex` is the offset of the first changed result, and reading
 * `event.results[event.resultIndex]` is the documented access pattern.
 */
interface SpeechRecognitionEvent extends Event {
  readonly resultIndex: number
  readonly results: SpeechRecognitionResultList
}

/**
 * Recognition lifecycle: no-speech, permission denial, network failure and
 * audio-capture failure are all reported through `onerror` rather than by
 * throwing, which is why the error code is a closed union.
 */
interface SpeechRecognitionErrorEvent extends Event {
  readonly error: SpeechRecognitionErrorCode
  readonly message: string
}

/**
 * `no-speech` is the common one and is not really an error: it means the
 * recogniser heard a window of audio and found nothing speech-like. It gets its
 * own UI copy rather than a generic failure message.
 */
type SpeechRecognitionErrorCode =
  | 'aborted'
  | 'audio-capture'
  | 'language-not-supported'
  | 'network'
  | 'no-speech'
  | 'not-allowed'
  | 'service-not-allowed'

interface SpeechRecognition extends EventTarget {
  /** BCP-47 tag. `undefined` means the browser default. */
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number

  start(): void
  stop(): void
  abort(): void

  onstart: ((this: SpeechRecognition, ev: Event) => unknown) | null
  onend: ((this: SpeechRecognition, ev: Event) => unknown) | null
  onresult: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => unknown) | null
  onerror: ((this: SpeechRecognition, ev: SpeechRecognitionErrorEvent) => unknown) | null
}

declare const SpeechRecognition: {
  prototype: SpeechRecognition
  new (): SpeechRecognition
}

interface Window {
  webkitSpeechRecognition?: typeof SpeechRecognition
}