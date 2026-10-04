import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  cancel,
  isSpeechSynthesisSupported,
  speak,
} from '@/features/assistant/speech-synthesis'
import type { VoiceError } from '@/features/assistant/types'

/**
 * jsdom implements no speech synthesis, so these tests drive a fake that records
 * the utterances handed to it. Recording rather than firing is deliberate: the
 * wrapper's job is to create the right utterance and to guarantee that callbacks
 * arrive exactly once, and a fake that fires events itself would be asserting
 * against its own choreography.
 */
class FakeUtterance {
  static instances: FakeUtterance[] = []

  text: string
  lang = ''
  rate = 1
  pitch = 1
  voice: SpeechSynthesisVoice | null = null

  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: ((event: SpeechSynthesisErrorEvent) => void) | null = null

  constructor(text: string) {
    this.text = text
    FakeUtterance.instances.push(this)
  }

  fireStart(): void {
    this.onstart?.()
  }

  fireEnd(): void {
    this.onend?.()
  }

  fireError(error: SpeechSynthesisErrorCode): void {
    this.onerror?.({ error } as SpeechSynthesisErrorEvent)
  }
}

class FakeSpeechSynthesis {
  speakCalls = 0
  cancelCalls = 0
  /** What the fake's `speak` should do; `undefined` means queue silently. */
  throwOnSpeak: Error | null = null

  constructor(private readonly voices: SpeechSynthesisVoice[] = []) {}

  speak(utterance: SpeechSynthesisUtterance): void {
    this.speakCalls += 1
    if (this.throwOnSpeak !== null) throw this.throwOnSpeak
    void utterance
  }

  cancel(): void {
    this.cancelCalls += 1
  }

  getVoices(): SpeechSynthesisVoice[] {
    return this.voices
  }
}

function voice(lang: string, name: string): SpeechSynthesisVoice {
  return {
    name,
    lang,
    localService: true,
    default: false,
    voiceURI: name,
  } as SpeechSynthesisVoice
}

function latestUtterance(): FakeUtterance {
  const utterance = FakeUtterance.instances.at(-1)
  if (utterance === undefined) throw new Error('no utterance was created')
  return utterance
}

function installFake(voices: SpeechSynthesisVoice[] = []): FakeSpeechSynthesis {
  const fake = new FakeSpeechSynthesis(voices)
  FakeUtterance.instances = []
  vi.stubGlobal('speechSynthesis', fake)
  vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance)
  return fake
}

interface Handlers {
  onStart: ReturnType<typeof vi.fn<() => void>>
  onEnd: ReturnType<typeof vi.fn<() => void>>
  onError: ReturnType<typeof vi.fn<(error: VoiceError) => void>>
}

function handlers(): Handlers {
  return { onStart: vi.fn(), onEnd: vi.fn(), onError: vi.fn() }
}

beforeEach(() => {
  installFake()
})

afterEach(() => {
  // Module state is shared across tests by design; leaving an active utterance
  // behind would make the next `speak` look like an overlap.
  cancel()
})

describe('isSpeechSynthesisSupported', () => {
  it('is true once the browser provides a usable speechSynthesis', () => {
    expect(isSpeechSynthesisSupported()).toBe(true)
  })

  it('is false without one, and does not throw asking', () => {
    vi.stubGlobal('speechSynthesis', undefined)
    expect(() => isSpeechSynthesisSupported()).not.toThrow()
    expect(isSpeechSynthesisSupported()).toBe(false)
  })

  it('is false for a stub without speak and cancel', () => {
    // A partial polyfill is worse than none: claiming support and failing on the
    // first utterance would show the user a control that never works.
    vi.stubGlobal('speechSynthesis', { getVoices: () => [] })
    expect(isSpeechSynthesisSupported()).toBe(false)
  })
})

describe('speak', () => {
  it('creates an utterance carrying the text and speaks it', () => {
    const handlers_ = handlers()
    const fake = installFake()

    expect(speak('Routing to TaskService', handlers_)).toBe(true)
    expect(fake.speakCalls).toBe(1)
    expect(latestUtterance().text).toBe('Routing to TaskService')
  })

  it('applies rate and pitch, defaulting both to the browser normal', () => {
    installFake()
    speak('show my tasks')
    expect(latestUtterance().rate).toBe(1)
    expect(latestUtterance().pitch).toBe(1)

    speak('show my tasks', { rate: 1.2, pitch: 0.8 })
    expect(latestUtterance().rate).toBe(1.2)
    expect(latestUtterance().pitch).toBe(0.8)
  })

  it('reports the start and the end through the callbacks', () => {
    const handlers_ = handlers()
    speak('routed to analytics', handlers_)

    const utterance = latestUtterance()
    utterance.fireStart()
    expect(handlers_.onStart).toHaveBeenCalledTimes(1)

    utterance.fireEnd()
    expect(handlers_.onEnd).toHaveBeenCalledTimes(1)
  })

  it('reports onEnd exactly once even if the browser fires end twice', () => {
    const handlers_ = handlers()
    speak('routed to analytics', handlers_)

    const utterance = latestUtterance()
    utterance.fireEnd()
    utterance.fireEnd()

    expect(handlers_.onEnd).toHaveBeenCalledTimes(1)
  })
})

describe('overlapping speech', () => {
  it('cancels the first utterance before starting the second', () => {
    // Without this the assistant talks over its own answer and the user cannot
    // tell which reply belongs to which question.
    const fake = installFake()
    const first = handlers()
    const second = handlers()

    speak('routing to TaskService', first)
    speak('routing to Analytics', second)

    expect(fake.cancelCalls).toBe(1)
    expect(fake.speakCalls).toBe(2)
  })

  it('settles the cancelled utterance before the replacement starts', () => {
    const order: string[] = []
    speak('first', { onEnd: () => order.push('end:first') })
    speak('second', { onStart: () => order.push('start:second') })

    latestUtterance().fireStart()
    expect(order).toEqual(['end:first', 'start:second'])
  })

  it('does not let a late end event from the cancelled utterance settle the new one', () => {
    // The browser fires `end` on an utterance we cancelled. If that reached the
    // replacement's owner, the assistant would drop out of `speaking` while the
    // second line was still being read.
    const first = handlers()
    const second = handlers()
    speak('first', first)
    const cancelled = latestUtterance()
    speak('second', second)

    cancelled.fireEnd()
    expect(second.onEnd).not.toHaveBeenCalled()

    latestUtterance().fireEnd()
    expect(second.onEnd).toHaveBeenCalledTimes(1)
  })

  it('does not treat the cancelled utterance end event as a failure', () => {
    const first = handlers()
    speak('first', first)
    const cancelled = latestUtterance()
    speak('second')
    cancelled.fireError('canceled')

    expect(first.onError).not.toHaveBeenCalled()
    expect(first.onEnd).toHaveBeenCalledTimes(1)
  })
})

describe('blank text', () => {
  it('refuses an empty string, and says so through the return value', () => {
    const fake = installFake()
    const handlers_ = handlers()

    expect(speak('', handlers_)).toBe(false)
    expect(fake.speakCalls).toBe(0)
    // Nothing was spoken, so nothing will ever fire: a caller waiting on `onEnd`
    // would hang forever, which is the wedge this refusal exists to prevent.
    expect(handlers_.onEnd).not.toHaveBeenCalled()
    expect(handlers_.onError).not.toHaveBeenCalled()
    expect(FakeUtterance.instances).toHaveLength(0)
  })

  it('refuses whitespace-only text for the same reason', () => {
    installFake()
    expect(speak('   \n\t ', handlers())).toBe(false)
    expect(FakeUtterance.instances).toHaveLength(0)
  })

  it('does not cancel a speaking utterance with a blank one', () => {
    // A blank request is not a new reply. Cancelling the line already being read
    // would make the assistant go silent on nothing.
    const fake = installFake()
    speak('routing to TaskService')
    expect(speak('   ')).toBe(false)
    expect(fake.cancelCalls).toBe(0)
    expect(fake.speakCalls).toBe(1)
  })
})

describe('voice selection', () => {
  it('prefers a voice whose tag matches the requested language', () => {
    installFake([voice('en-GB', 'Serena'), voice('de-DE', 'Anna')])
    speak('guten tag', { lang: 'de-DE' })
    expect(latestUtterance().voice?.name).toBe('Anna')
  })

  it('falls back to the same base language when the exact tag is absent', () => {
    installFake([voice('en-GB', 'Serena')])
    speak('show my tasks', { lang: 'en-US' })
    expect(latestUtterance().voice?.name).toBe('Serena')
  })

  it('falls back to the browser default rather than failing when no voice matches', () => {
    // A wrong accent is a better outcome than no spoken reply at all.
    const fake = installFake([voice('de-DE', 'Anna')])
    expect(speak('show my tasks', { lang: 'en-GB' })).toBe(true)
    expect(fake.speakCalls).toBe(1)
    expect(latestUtterance().voice).toBeNull()
  })

  it('survives a browser whose voice list has not loaded yet', () => {
    installFake([])
    expect(() => speak('show my tasks', { lang: 'en-GB' })).not.toThrow()
    expect(latestUtterance().voice).toBeNull()
  })

  it('lets an explicit voice win over the language', () => {
    installFake([voice('en-GB', 'Serena')])
    speak('show my tasks', { lang: 'de-DE', voice: voice('en-GB', 'Serena') })
    expect(latestUtterance().voice?.name).toBe('Serena')
  })
})

describe('synthesis errors', () => {
  it('maps every browser code to a synthesis_failed the user can act on', () => {
    const CODES: ReadonlyArray<SpeechSynthesisErrorCode> = [
      'audio-busy',
      'audio-hardware',
      'canceled',
      'interrupted',
      'invalid-argument',
      'language-unavailable',
      'network',
      'not-allowed',
      'synthesis-failed',
      'synthesis-unavailable',
      'text-too-long',
      'voice-unavailable',
    ]
    for (const code of CODES) {
      const handlers_ = handlers()
      speak('routing to TaskService', handlers_)
      latestUtterance().fireError(code)

      if (code === 'canceled' || code === 'interrupted') {
        expect(handlers_.onError, code).not.toHaveBeenCalled()
        expect(handlers_.onEnd, code).toHaveBeenCalledTimes(1)
        continue
      }

      expect(handlers_.onError, code).toHaveBeenCalledTimes(1)
      const reported = handlers_.onError.mock.calls[0]?.[0]
      expect(reported?.code, code).toBe('synthesis_failed')
      expect(reported?.message, code).toBeTruthy()
      // The browser's own vocabulary is implementation detail: the message is a
      // sentence, never the code handed to us.
      expect(reported?.message, code).not.toBe(code)
      expect(reported?.message, code).toMatch(/\.$/)
      expect(handlers_.onEnd, code).toHaveBeenCalledTimes(1)
    }
  })

  it('reports a browser that throws instead of failing silently', () => {
    const fake = installFake()
    fake.throwOnSpeak = new Error('queue in a state the browser disliked')
    const handlers_ = handlers()

    expect(speak('routing to TaskService', handlers_)).toBe(false)
    expect(handlers_.onError).toHaveBeenCalledTimes(1)
    expect(handlers_.onError.mock.calls[0]?.[0].code).toBe('synthesis_failed')
    // onEnd still runs: a caller leaving a `speaking` state must not be stranded
    // by the one path that never reaches the browser.
    expect(handlers_.onEnd).toHaveBeenCalledTimes(1)
  })

  it('reports an unsupported browser as a failure rather than pretending to speak', () => {
    vi.stubGlobal('speechSynthesis', undefined)
    const handlers_ = handlers()

    expect(speak('routing to TaskService', handlers_)).toBe(false)
    expect(handlers_.onError).toHaveBeenCalledTimes(1)
    expect(handlers_.onError.mock.calls[0]?.[0].code).toBe('synthesis_failed')
    expect(handlers_.onError.mock.calls[0]?.[0].retryable).toBe(false)
    expect(handlers_.onEnd).not.toHaveBeenCalled()
  })
})

describe('cancel', () => {
  it('stops the utterance and settles it exactly once', () => {
    const fake = installFake()
    const handlers_ = handlers()
    speak('routing to TaskService', handlers_)

    cancel()

    expect(fake.cancelCalls).toBe(1)
    expect(handlers_.onEnd).toHaveBeenCalledTimes(1)

    // Browsers fire `end` after a cancel. The wrapper settled the utterance
    // first, so the extra event must not double-report.
    latestUtterance().fireEnd()
    expect(handlers_.onEnd).toHaveBeenCalledTimes(1)
  })

  it('is safe when nothing is speaking', () => {
    installFake()
    expect(() => cancel()).not.toThrow()
    cancel()
  })

  it('lets a new utterance speak cleanly after a cancel', () => {
    const fake = installFake()
    speak('first')
    cancel()
    speak('second')

    expect(fake.cancelCalls).toBe(1)
    expect(fake.speakCalls).toBe(2)
  })
})