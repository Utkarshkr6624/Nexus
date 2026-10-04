import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RoutingStatusBadge, VoiceStatePill } from '@/features/assistant/components/voice-state-pill'
import type { VoiceState } from '@/features/assistant/types'
import type { RoutingStatus } from '@/types/ml'

/**
 * The lifecycle chips.
 *
 * **Every claim here is a reader's claim, not a renderer's.** Six states that
 * differ mostly in motion and hue are the exact place a colour-only indicator
 * fails — in dark mode, for a colour-vision-deficient reader, in print, and for a
 * screen reader, where a pulsing span is silence — so the assertions are on the
 * *shape* of the glyph and on the *word* beside it. Asserting the lucide class is
 * deliberate: it is the only stable handle on which of several visually similar
 * outlines was drawn, and "some icon appeared" is not a test.
 */

const originalMatchMedia = window.matchMedia

function preferReducedMotion(): void {
  window.matchMedia = ((query: string) => ({
    media: query,
    matches: query.includes('prefers-reduced-motion'),
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

function restoreMatchMedia(): void {
  window.matchMedia = originalMatchMedia
}

/** The glyph's lucide class, which names the icon rather than just its category. */
function glyphOf(container: HTMLElement): string {
  const svg = container.querySelector('svg')
  expect(svg).not.toBeNull()
  return svg?.getAttribute('class') ?? ''
}

const CASES: Array<{ state: VoiceState; word: string; glyph: string }> = [
  { state: 'idle', word: 'Tap to speak', glyph: 'lucide-mic' },
  { state: 'listening', word: 'Listening…', glyph: 'lucide-radio' },
  { state: 'processing', word: 'Processing…', glyph: 'lucide-loader' },
  { state: 'speaking', word: 'Speaking…', glyph: 'lucide-volume-2' },
  { state: 'error', word: 'Could not finish that', glyph: 'lucide-triangle-alert' },
  { state: 'unsupported', word: 'Speech input unavailable', glyph: 'lucide-ban' },
]

describe('VoiceStatePill', () => {
  it('gives every state a shape-distinct glyph beside its word', () => {
    const glyphs: string[] = []

    for (const { state, word, glyph } of CASES) {
      const { container, unmount } = render(<VoiceStatePill state={state} />)

      expect(screen.getByText(word)).toBeInTheDocument()
      expect(glyphOf(container)).toContain(glyph)
      // Decorative: the word already names the state, so announcing the glyph
      // as well would be a duplicate rather than extra information.
      expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')

      glyphs.push(glyph)
      unmount()
    }

    // Six states, six outlines. A shared glyph would mean a reader who cannot
    // see colour has fewer words than the design thinks they do.
    expect(new Set(glyphs).size).toBe(CASES.length)
  })

  it('pulses while listening, and drops the pulse for reduced motion', () => {
    try {
      const { container } = render(<VoiceStatePill state="listening" />)
      expect(container.querySelector('svg')).toHaveClass('animate-pulse')

      preferReducedMotion()
      const reduced = render(<VoiceStatePill state="listening" />)
      expect(reduced.container.querySelector('svg')).not.toHaveClass('animate-pulse')
    } finally {
      restoreMatchMedia()
    }
  })

  it('does not animate a state that is not listening', () => {
    const { container } = render(<VoiceStatePill state="idle" />)
    expect(container.querySelector('svg')).not.toHaveClass('animate-pulse')
  })

  it('maps each state onto a badge tone without colour carrying the meaning alone', () => {
    // The word is what a reader gets; the tone is a second, redundant channel.
    const { container: idle } = render(<VoiceStatePill state="idle" />)
    expect(idle.querySelector('span')).toHaveClass('bg-secondary')

    const { container: busy } = render(<VoiceStatePill state="processing" />)
    expect(busy.querySelector('span')).toHaveClass('bg-primary/15')

    const { container: broken } = render(<VoiceStatePill state="error" />)
    expect(broken.querySelector('span')).toHaveClass('text-destructive')
  })

  it('renders the working state with the shared spinner rather than a bare glyph', () => {
    render(<VoiceStatePill state="processing" />)
    // The badge word is the accessible content; a second label inside the same
    // chip would be announced twice.
    expect(screen.getByRole('status')).toHaveTextContent('')
  })
})

const STATUS_CASES: Array<{ status: RoutingStatus; word: string; glyph: string }> = [
  { status: 'accepted', word: 'Routed', glyph: 'lucide-circle-check' },
  { status: 'uncertain', word: 'Not confident enough', glyph: 'lucide-circle-help' },
  { status: 'out_of_scope', word: 'Out of scope', glyph: 'lucide-ban' },
  { status: 'generation_unavailable', word: 'Needs generation', glyph: 'lucide-triangle-alert' },
]

describe('RoutingStatusBadge', () => {
  it('names what NEXO did, with a distinct shape for each outcome', () => {
    for (const { status, word, glyph } of STATUS_CASES) {
      const { container, unmount } = render(<RoutingStatusBadge status={status} />)
      expect(screen.getByText(word)).toBeInTheDocument()
      expect(glyphOf(container)).toContain(glyph)
      unmount()
    }
  })

  it('does not dress a capability gap as a failure', () => {
    // Nothing went wrong: the classifier recognised the request correctly and
    // NEXUS has nowhere to send it. `destructive` would report a product
    // decision as a fault.
    const { container } = render(<RoutingStatusBadge status="generation_unavailable" />)
    expect(container.querySelector('span')).not.toHaveClass('text-destructive')
  })
})
