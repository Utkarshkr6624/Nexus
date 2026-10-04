import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MarkdownView } from '@/features/knowledge/markdown'

/**
 * `MarkdownView` is the single reading surface: notes, concepts and the editor
 * preview all go through it. Two of its claims are load-bearing enough to be
 * worth a test that can fail.
 *
 * **It is the one place user-authored text becomes HTML.** That is only safe
 * because `renderMarkdown` escapes before it emits and sanitises on the way
 * out, so a note containing `<script>` must appear on screen as the literal
 * text a person typed rather than as an element. The test asserts the *absence*
 * of the element, not the shape of the string, because the shape is an
 * implementation detail and the element is the actual harm.
 *
 * **Empty source renders nothing.** A page that mounted it around a missing
 * body used to get an empty styled box; `null` is the honest answer and the
 * pages around it already branch on having content.
 */

describe('MarkdownView', () => {
  it('renders the Markdown a person wrote', () => {
    render(<MarkdownView content={'# Title\n\nA **bold** claim.'} />)

    expect(screen.getByRole('heading', { name: 'Title' })).toBeDefined()
    expect(screen.getByText('bold').tagName).toBe('STRONG')
  })

  it('shows a script tag as text rather than running it', () => {
    const { container } = render(<MarkdownView content={'<script>alert(1)</script>'} />)

    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('<script>alert(1)</script>')
  })

  it('drops a javascript: link instead of emitting an anchor to it', () => {
    const { container } = render(<MarkdownView content={'[click](javascript:alert(1))'} />)

    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).toContain('click')
  })

  it.each([[null], [undefined], ['']])('renders nothing for %p', (content) => {
    const { container } = render(<MarkdownView content={content} />)

    expect(container.innerHTML).toBe('')
  })

  it('adds the caller classes alongside its own', () => {
    const { container } = render(<MarkdownView content="text" className="max-w-[68ch]" />)

    expect(container.firstElementChild?.className).toContain('max-w-[68ch]')
  })
})
