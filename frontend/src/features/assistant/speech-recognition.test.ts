import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  RECOGNITION_PRIVACY_NOTICE,
  createSpeechRecogniser,
  isSpeechRecognitionSupported,
} from '@/features/assistant/speech-recognition'
import { RECOGNITION_TIMEOUT_MS } from '@/features/assistant/types'
import type { VoiceError } from '@/features/assistant/types'

/**
 * jsdom implements no speech recognition at all, so every test drives a fake.
 *
 * The fake is deliberately literal — a counter for each method and a method to
 * fire each event — because the wrapper's whole job is translating browser
 * callbacks into assistant callbacks. Anything cleverer here would test the
 * fake rather than the wrapper.
 */
class FakeSpeechRecognition {
  static instances: FakeSpeechRecognition[] = []

  lang = ''
  continuous = false
  interimResults = false
  maxAlternatives = 0

  onstart: ((this: SpeechRecognition, ev: Event) => unknown) | null = null
  onend: ((this: SpeechRecognition, ev: Event) => unknown) | null = null
  onresult: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => unknown) | null = null
  onerror: ((this: SpeechRecognition, ev: SpeechRecognitionErrorEvent) => unknown) | null = null

  startCalls = 0
  stopCalls = 0
  abortCalls = 0

  constructor() {
    FakeSpeechRecognition.instances.push(this)
  }

  start(): void {
    this.startCalls += 1
  }

  stop(): void {
    this.stopCalls += 1
  }

  abort(): void {
    this.abortCalls += 1
  }

  emitResult(
    resultIndex: number,
    entries: ReadonlyArray<{ transcript: string; isFinal: boolean }>,
  ): void {
    this.onresult?.call(this as unknown as SpeechRecognition, buildResultEvent(resultIndex, entries))
  }

  emitError(error: SpeechRecognitionErrorCode): void {
    this.onerror?.call(this as unknown as SpeechRecognition, {
      error,
      message: `raw browser message for ${error}`,
    } as unknown as SpeechRecognitionErrorEvent)
  }

  emitEnd(): void {
    this.onend?.call(this as unknown as SpeechRecognition, new Event('end'))
  }
}

function buildResultEvent(
  resultIndex: number,
  entries: ReadonlyArray<{ transcript: string; isFinal: boolean }>,
): SpeechRecognitionEvent {
  const results = entries.map((entry) => {
    const alternative: SpeechRecognitionAlternative = {
      transcript: entry.transcript,
      confidence: 0.92,
    }
    return {
      isFinal: entry.isFinal,
      length: 1,
      item: () => alternative,
      0: alternative,
      [Symbol.iterator]: function* () {
        yield alternative
      },
    } as unknown as SpeechRecognitionResult
  })
  const indexed: Record<number, SpeechRecognitionResult> = {}
  results.forEach((result, index) => {
    indexed[index] = result
  })
  const list = {
    length: results.length,
    item: (index: number) => results[index] as SpeechRecognitionResult,
    ...indexed,
    [Symbol.iterator]: function* () {
      yield* results
    },
  } as unknown as SpeechRecognitionResultList
  return { resultIndex, results: list } as unknown as SpeechRecognitionEvent
}

function installFake(): void {
  FakeSpeechRecognition.instances = []
  vi.stubGlobal('SpeechRecognition', FakeSpeechRecognition)
}

/** The instance the wrapper created for its most recent `start()`. */
function latestInstance(): FakeSpeechRecognition {
  const instance = FakeSpeechRecognition.instances.at(-1)
  if (instance === undefined) throw new Error('no recogniser was created')
  return instance
}

interface Handlers {
  onInterim: ReturnType<typeof vi.fn<(text: string) => void>>
  onFinal: ReturnType<typeof vi.fn<(transcript: string) => void>>
  onError: ReturnType<typeof vi.fn<(error: VoiceError) => void>>
  onEnd: ReturnType<typeof vi.fn<() => void>>
}

function handlers(): Handlers {
  return { onInterim: vi.fn(), onFinal: vi.fn(), onError: vi.fn(), onEnd: vi.fn() }
}

beforeEach(() => {
  installFake()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('isSpeechRecognitionSupported', () => {
  it('is true for the spec spelling', () => {
    expect(isSpeechRecognitionSupported()).toBe(true)
  })

  it('is true for the webkit spelling, which is all Safari ships', () => {
    vi.stubGlobal('SpeechRecognition', undefined)
    vi.stubGlobal('webkitSpeechRecognition', FakeSpeechRecognition)
    expect(isSpeechRecognitionSupported()).toBe(true)
  })

  it('is false when neither exists, and does not throw asking', () => {
    // The unsupported state is resolved at mount, so this call runs on browsers
    // that will never speak again — it has to be answerable, not exceptional.
    vi.unstubAllGlobals()
    expect(() => isSpeechRecognitionSupported()).not.toThrow()
    expect(isSpeechRecognitionSupported()).toBe(false)
  })

  it('rejects a global that exists but is not a constructor', () => {
    // A truthy-but-unusable value would otherwise reach `new` and throw during
    // render, which is the one place a throw cannot be handled gracefully.
    vi.stubGlobal('SpeechRecognition', { notAConstructor: true })
    expect(isSpeechRecognitionSupported()).toBe(false)
  })
})

describe('createSpeechRecogniser', () => {
  it('creates nothing until start, and one instance per session', () => {
    const { onFinal, onEnd } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal, onEnd })

    expect(FakeSpeechRecognition.instances).toHaveLength(0)

    recogniser.start()
    expect(FakeSpeechRecognition.instances).toHaveLength(1)
    const first = latestInstance()
    expect(first.startCalls).toBe(1)

    first.emitResult(0, [{ transcript: 'show my tasks', isFinal: true }])
    first.emitEnd()

    recogniser.start()
    expect(FakeSpeechRecognition.instances).toHaveLength(2)
    expect(latestInstance()).not.toBe(first)
  })

  it('configures a single-utterance session, because the model behind it is single-utterance', () => {
    const recogniser = createSpeechRecogniser({ lang: 'en-GB', interimResults: false })
    recogniser.start()

    const instance = latestInstance()
    expect(instance.lang).toBe('en-GB')
    expect(instance.continuous).toBe(false)
    expect(instance.maxAlternatives).toBe(1)
    expect(instance.interimResults).toBe(false)
  })

  it('defaults interim results on, so the UI can show live feedback', () => {
    const recogniser = createSpeechRecogniser()
    recogniser.start()
    expect(latestInstance().interimResults).toBe(true)
  })

  it('leaves lang untouched when the caller names none, keeping the browser default', () => {
    const recogniser = createSpeechRecogniser()
    recogniser.start()
    // The fake's default is the empty string; the wrapper must not have written.
    expect(latestInstance().lang).toBe('')
  })

  it('ignores a second start rather than letting the browser throw InvalidStateError', () => {
    const { onError } = handlers()
    const recogniser = createSpeechRecogniser({ onError })

    recogniser.start()
    recogniser.start()
    recogniser.start()

    expect(FakeSpeechRecognition.instances).toHaveLength(1)
    expect(latestInstance().startCalls).toBe(1)
    // A double-click on the assistant's own button is not a failure to report.
    expect(onError).not.toHaveBeenCalled()
  })

  it('reports not_supported and ends the session when the browser has no recogniser', () => {
    vi.stubGlobal('SpeechRecognition', undefined)
    vi.stubGlobal('webkitSpeechRecognition', undefined)
    const { onError, onEnd } = handlers()

    const recogniser = createSpeechRecogniser({ onError, onEnd })
    expect(() => recogniser.start()).not.toThrow()

    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0]?.[0].code).toBe('not_supported')
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it('stops through the browser, so a final result can still be flushed', () => {
    const { onFinal } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal })

    recogniser.start()
    recogniser.stop()

    const instance = latestInstance()
    expect(instance.stopCalls).toBe(1)
    expect(instance.abortCalls).toBe(0)

    instance.emitResult(0, [{ transcript: 'log my hours', isFinal: true }])
    expect(onFinal).toHaveBeenCalledWith('log my hours')
  })

  it('aborts through the browser when the utterance is being discarded', () => {
    const recogniser = createSpeechRecogniser()
    recogniser.start()
    recogniser.abort()

    const instance = latestInstance()
    expect(instance.abortCalls).toBe(1)
    expect(instance.stopCalls).toBe(0)
  })

  it('treats stop and abort before any start as no-ops', () => {
    const { onError, onEnd } = handlers()
    const recogniser = createSpeechRecogniser({ onError, onEnd })

    expect(() => {
      recogniser.stop()
      recogniser.abort()
    }).not.toThrow()
    expect(FakeSpeechRecognition.instances).toHaveLength(0)
    expect(onError).not.toHaveBeenCalled()
    expect(onEnd).not.toHaveBeenCalled()
  })
})

describe('recognition results', () => {
  it('passes a final transcript through, trimmed', () => {
    const { onFinal } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal })
    recogniser.start()

    latestInstance().emitResult(0, [{ transcript: '  show my open tasks  ', isFinal: true }])

    expect(onFinal).toHaveBeenCalledTimes(1)
    expect(onFinal).toHaveBeenCalledWith('show my open tasks')
  })

  it('passes interim text to onInterim and never to onFinal', () => {
    const { onInterim, onFinal } = handlers()
    const recogniser = createSpeechRecogniser({ onInterim, onFinal })
    recogniser.start()

    const instance = latestInstance()
    instance.emitResult(0, [{ transcript: 'show my ', isFinal: false }])
    instance.emitResult(0, [{ transcript: 'show my open ', isFinal: false }])

    expect(onInterim.mock.calls).toEqual([['show my'], ['show my open']])
    expect(onFinal).not.toHaveBeenCalled()
  })

  it('uses the final result and ignores the interims shipped alongside it', () => {
    // One dispatch can carry several results. Only the final one is a finished
    // utterance; the interims before it were hypotheses the browser has replaced.
    const { onInterim, onFinal } = handlers()
    const recogniser = createSpeechRecogniser({ onInterim, onFinal })
    recogniser.start()

    latestInstance().emitResult(1, [
      { transcript: 'this week', isFinal: false },
      { transcript: ' ', isFinal: false },
      { transcript: 'and last week', isFinal: true },
    ])

    expect(onFinal).toHaveBeenCalledTimes(1)
    expect(onFinal).toHaveBeenCalledWith('and last week')
    expect(onInterim).not.toHaveBeenCalled()
  })

  it('prefers the last of several finals rather than joining competing hypotheses', () => {
    const { onFinal } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal })
    recogniser.start()

    latestInstance().emitResult(0, [
      { transcript: 'first guess', isFinal: true },
      { transcript: 'second guess', isFinal: true },
    ])

    expect(onFinal).toHaveBeenCalledTimes(1)
    expect(onFinal).toHaveBeenCalledWith('second guess')
  })

  it('survives a resultIndex past the end of the list', () => {
    // A session torn down mid-dispatch can deliver an index the list does not
    // have. That must be an empty result, not a thrown TypeError.
    const { onFinal, onError } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal, onError })
    recogniser.start()

    expect(() => latestInstance().emitResult(7, [])).not.toThrow()
    expect(onFinal).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('survives a result with no alternatives at index 0', () => {
    const { onFinal, onInterim, onError } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal, onInterim, onError })
    recogniser.start()

    const instance = latestInstance()
    expect(() =>
      instance.onresult?.call(instance as unknown as SpeechRecognition, {
        resultIndex: 0,
        results: {
          length: 1,
          item: () => ({ isFinal: false, length: 0, item: () => undefined }) as never,
        } as unknown as SpeechRecognitionResultList,
      } as unknown as SpeechRecognitionEvent),
    ).not.toThrow()
    expect(onFinal).not.toHaveBeenCalled()
    expect(onInterim).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('reports a whitespace-only final result as no_speech, not as an empty transcript', () => {
    // An empty string is a request with no content; sending it would earn a 422
    // that says far less than "I didn't hear anything".
    const { onFinal, onError } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal, onError })
    recogniser.start()

    latestInstance().emitResult(0, [{ transcript: '   ', isFinal: true }])

    expect(onFinal).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0]?.[0].code).toBe('no_speech')
  })

  it('reports nothing for an interim result that trims to nothing', () => {
    const { onInterim, onError } = handlers()
    const recogniser = createSpeechRecogniser({ onInterim, onError })
    recogniser.start()

    latestInstance().emitResult(0, [{ transcript: ' ', isFinal: false }])

    expect(onInterim).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })
})

describe('recognition error mapping', () => {
  const CASES: ReadonlyArray<readonly [SpeechRecognitionErrorCode, VoiceError['code']]> = [
    ['not-allowed', 'permission_denied'],
    ['service-not-allowed', 'permission_denied'],
    ['audio-capture', 'microphone_unavailable'],
    ['no-speech', 'no_speech'],
    ['network', 'recognition_failed'],
    ['language-not-supported', 'not_supported'],
  ]

  it.each(CASES)('maps %s to %s', (browserCode, expected) => {
    const { onError } = handlers()
    const recogniser = createSpeechRecogniser({ onError })
    recogniser.start()

    latestInstance().emitError(browserCode)

    expect(onError).toHaveBeenCalledTimes(1)
    const reported = onError.mock.calls[0]?.[0]
    expect(reported?.code).toBe(expected)
    expect(reported?.message).toBeTruthy()
    expect(typeof reported?.retryable).toBe('boolean')
  })

  it('covers every code in the closed union, so no browser code can escape unmapped', () => {
    const ALL: ReadonlyArray<SpeechRecognitionErrorCode> = [
      'aborted',
      'audio-capture',
      'language-not-supported',
      'network',
      'no-speech',
      'not-allowed',
      'service-not-allowed',
    ]
    const mapped = new Set(CASES.map(([browserCode]) => browserCode))
    // `aborted` is asserted separately: it must not be mapped at all.
    expect(ALL.filter((code) => !mapped.has(code))).toEqual(['aborted'])
  })

  it('never leaks the browser code or its raw message into user-facing copy', () => {
    for (const [browserCode] of CASES) {
      const { onError } = handlers()
      const recogniser = createSpeechRecogniser({ onError })
      recogniser.start()
      latestInstance().emitError(browserCode)

      const message = onError.mock.calls[0]?.[0].message ?? ''
      // The browser's own `message` field is a diagnostic string, never copy.
      expect(message, browserCode).not.toContain('raw browser message')
      // And the code is never shown as-is: a user-facing message is a sentence,
      // not a token.
      expect(message, browserCode).not.toBe(browserCode)
      expect(message, browserCode).toMatch(/\w\.|\.$/)
    }
  })

  it('blames the recognition service for a network failure, not NEXO', () => {
    // The most misreadable code in the set: "network" is the *vendor's* service,
    // not the user's connectivity to NEXO. A message that blamed the wrong one
    // would send someone debugging the wrong layer.
    const { onError } = handlers()
    const recogniser = createSpeechRecogniser({ onError })
    recogniser.start()

    latestInstance().emitError('network')

    const message = onError.mock.calls[0]?.[0].message ?? ''
    expect(message).toMatch(/recognition/i)
    expect(message).toMatch(/could not be reached/i)
    expect(message).not.toMatch(/NEXO/i)
  })

  it('treats aborted as a normal stop, so cancelling never looks like a failure', () => {
    const { onError, onEnd } = handlers()
    const recogniser = createSpeechRecogniser({ onError, onEnd })
    recogniser.start()

    const instance = latestInstance()
    recogniser.abort()
    instance.emitError('aborted')
    instance.emitEnd()

    expect(onError).not.toHaveBeenCalled()
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it('calls onEnd once per session, whatever ended it', () => {
    const { onEnd } = handlers()
    const recogniser = createSpeechRecogniser({ onEnd })

    recogniser.start()
    latestInstance().emitEnd()
    latestInstance().emitEnd()

    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it('allows a new session after one ends', () => {
    const { onFinal } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal })

    recogniser.start()
    latestInstance().emitResult(0, [{ transcript: 'first', isFinal: true }])
    latestInstance().emitEnd()

    recogniser.start()
    latestInstance().emitResult(0, [{ transcript: 'second', isFinal: true }])

    expect(onFinal.mock.calls).toEqual([['first'], ['second']])
  })
})

describe('recognition timeout', () => {
  it('aborts the session and reports timeout when the deadline passes', () => {
    vi.useFakeTimers()
    const { onError, onEnd } = handlers()
    const recogniser = createSpeechRecogniser({ onError, onEnd })
    recogniser.start()

    vi.advanceTimersByTime(RECOGNITION_TIMEOUT_MS)

    const instance = latestInstance()
    expect(instance.abortCalls).toBe(1)
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0]?.[0].code).toBe('timeout')
    expect(onError.mock.calls[0]?.[0].retryable).toBe(true)
    expect(onEnd).not.toHaveBeenCalled()
  })

  it('says what stopped it, because the microphone indicator still being on is the question', () => {
    vi.useFakeTimers()
    const { onError } = handlers()
    const recogniser = createSpeechRecogniser({ onError })
    recogniser.start()

    vi.advanceTimersByTime(RECOGNITION_TIMEOUT_MS)
    expect(onError.mock.calls[0]?.[0].message).toMatch(/stopped listening/i)
  })

  it('does not fire before the deadline', () => {
    vi.useFakeTimers()
    const { onError } = handlers()
    const recogniser = createSpeechRecogniser({ onError })
    recogniser.start()

    vi.advanceTimersByTime(RECOGNITION_TIMEOUT_MS - 1)
    expect(onError).not.toHaveBeenCalled()
    expect(latestInstance().abortCalls).toBe(0)
  })

  it('is cancelled by a normal end, so a finished utterance is never reported as late', () => {
    vi.useFakeTimers()
    const { onFinal, onError } = handlers()
    const recogniser = createSpeechRecogniser({ onFinal, onError })
    recogniser.start()

    const instance = latestInstance()
    instance.emitResult(0, [{ transcript: 'show my tasks', isFinal: true }])
    instance.emitEnd()

    vi.advanceTimersByTime(RECOGNITION_TIMEOUT_MS * 2)
    expect(onFinal).toHaveBeenCalledWith('show my tasks')
    expect(onError).not.toHaveBeenCalled()
  })

  it('is cancelled by an error, so one failure is reported once', () => {
    vi.useFakeTimers()
    const { onError } = handlers()
    const recogniser = createSpeechRecogniser({ onError })
    recogniser.start()

    latestInstance().emitError('audio-capture')
    vi.advanceTimersByTime(RECOGNITION_TIMEOUT_MS)

    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0]?.[0].code).toBe('microphone_unavailable')
  })

  it('does not fire for a session that was never started', () => {
    vi.useFakeTimers()
    const { onError } = handlers()
    createSpeechRecogniser({ onError })

    vi.advanceTimersByTime(RECOGNITION_TIMEOUT_MS * 2)
    expect(onError).not.toHaveBeenCalled()
  })
})

describe('the privacy disclosure', () => {
  it('states plainly that audio leaves the device', () => {
    // The disclosure is the whole reason this wrapper is acceptable, so it is
    // asserted rather than trusted: a reword that drops "leaves this device"
    // turns a limitation into a reassurance.
    expect(RECOGNITION_PRIVACY_NOTICE).toMatch(/leaves this device/i)
  })

  it('names the browsers that upload and the one that cannot listen', () => {
    expect(RECOGNITION_PRIVACY_NOTICE).toContain('Chrome')
    expect(RECOGNITION_PRIVACY_NOTICE).toContain('Edge')
    expect(RECOGNITION_PRIVACY_NOTICE).toContain('Firefox')
  })

  it('does not claim recognition is local', () => {
    expect(RECOGNITION_PRIVACY_NOTICE).not.toMatch(/on your (device|machine)/i)
    expect(RECOGNITION_PRIVACY_NOTICE).not.toMatch(/locally|privacy-friendly/i)
  })
})