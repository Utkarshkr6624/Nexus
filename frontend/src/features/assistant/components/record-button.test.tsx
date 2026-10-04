import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { RecordButton } from '@/features/assistant/components/record-button'
import type { VoiceState } from '@/features/assistant/types'

/**
 * The start/stop control.
 *
 * **The three claims worth protecting are about behaviour, not appearance.** A
 * microphone that is capturing audio has to look like it, because nothing else
 * on the page says so; it has to stop looking like it for a reader who asked for
 * reduced motion, who would otherwise get a pulsing icon with no way to tell it
 * apart from decoration; and it must not offer a turn that cannot start. The
 * destructive-versus-default split is asserted on the emitted classes rather than
 * on the variant prop, because `css: false` means the class string is the whole
 * of what a reader's browser will see.
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

function renderButton(state: VoiceState, handlers: { onStart?: () => void; onStop?: () => void } = {}) {
  return render(
    <RecordButton
      state={state}
      onStart={handlers.onStart ?? (() => undefined)}
      onStop={handlers.onStop ?? (() => undefined)}
    />,
  )
}

function button(): HTMLElement {
  return screen.getByRole('button')
}

describe('RecordButton', () => {
  it('offers the microphone in the ordinary style while idle', () => {
    renderButton('idle')

    expect(button()).toHaveClass('bg-primary')
    expect(button()).not.toHaveClass('bg-destructive')
    expect(button()).toBeEnabled()
    expect(screen.getByText('Tap to speak')).toBeInTheDocument()
    expect(button().querySelector('svg')).toHaveClass('lucide-mic')
  })

  it('turns destructive while recording, because the microphone is open', () => {
    renderButton('listening')

    expect(button()).toHaveClass('bg-destructive')
    expect(button()).toHaveAccessibleName('Stop listening and classify what was heard')
    expect(button().querySelector('svg')).toHaveClass('lucide-mic')
  })

  it('pulses the microphone only while it is recording', () => {
    const idle = renderButton('idle')
    expect(idle.container.querySelector('svg')).not.toHaveClass('animate-pulse')
    idle.unmount()

    const listening = renderButton('listening')
    expect(listening.container.querySelector('svg')).toHaveClass('animate-pulse')
  })

  it('drops the pulse for a reader who asked for reduced motion', () => {
    try {
      preferReducedMotion()
      const { container } = renderButton('listening')

      // The information the pulse carried is still available: the pill reads
      // "Listening…", so nothing is lost when the motion is gone.
      expect(container.querySelector('svg')).not.toHaveClass('animate-pulse')
      expect(button()).toHaveAccessibleName('Stop listening and classify what was heard')
    } finally {
      restoreMatchMedia()
    }
  })

  it('shows a spinner and refuses a second turn while it is classifying', () => {
    renderButton('processing')

    expect(button()).toBeDisabled()
    expect(button()).toHaveAccessibleName('Waiting for the classifier')
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.getByText('Classifying…')).toBeInTheDocument()
  })

  it('is unavailable, and says so, where the browser has no recogniser', () => {
    renderButton('unsupported')

    expect(button()).toBeDisabled()
    expect(button()).toHaveAccessibleName('Speech input is not available in this browser')
    expect(button().querySelector('svg')).toHaveClass('lucide-mic-off')
  })

  it('is unavailable while the browser is still speaking', () => {
    renderButton('speaking')

    expect(button()).toBeDisabled()
    expect(button().querySelector('svg')).toHaveClass('lucide-volume-2')
  })

  it('offers a retry rather than a dead end after a failure', () => {
    renderButton('error')

    expect(button()).toBeEnabled()
    expect(screen.getByText('Try again')).toBeInTheDocument()
  })

  it('starts a turn from idle and stops one from listening', async () => {
    const onStart = vi.fn()
    const onStop = vi.fn()
    const user = userEvent.setup()

    const { rerender } = renderButton('idle', { onStart, onStop })
    await user.click(button())
    expect(onStart).toHaveBeenCalledTimes(1)
    expect(onStop).not.toHaveBeenCalled()

    rerender(
      <RecordButton state="listening" onStart={onStart} onStop={onStop} />,
    )
    await user.click(button())
    expect(onStop).toHaveBeenCalledTimes(1)
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  it('does not call anything when the turn cannot begin', async () => {
    const onStart = vi.fn()
    const user = userEvent.setup()

    renderButton('unsupported', { onStart })
    await user.click(button())

    expect(onStart).not.toHaveBeenCalled()
  })
})
