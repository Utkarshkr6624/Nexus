import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { getModule } from '@/features/modules/catalog'
import AssistantPage from '@/pages/assistant-page'

/**
 * The route, asserted for what it promises on arrival.
 *
 * The one claim worth making here is negative: a page that opens with a
 * microphone reads as a chatbot, and every copy on it has to correct that before
 * the reader decides what they are looking at. It also has to stop saying
 * "Planned" — the catalog entry is phase 12 and the surface is built, so the
 * placeholder's badge would be a lie in the other direction.
 */

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/assistant']}>
        <AssistantPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('AssistantPage', () => {
  it('renders exactly one masthead and names the surface', () => {
    renderPage()

    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'AI Assistant' })).toBeInTheDocument()
    expect(screen.getByText('Platform')).toBeInTheDocument()
  })

  it('says what the assistant does before the reader presses anything', () => {
    renderPage()

    // The honest one-liner: it routes a request, it does not answer one. The
    // panel repeats it, because the header scrolls away and the microphone does not.
    expect(screen.getByText(/names the service behind it/)).toBeInTheDocument()
    expect(screen.getAllByText(/does not write an answer/).length).toBeGreaterThan(1)
  })

  it('names the single model NEXO runs', () => {
    renderPage()
    expect(screen.getByText('microsoft/deberta-v3-base')).toBeInTheDocument()
  })

  it('is not still described as planned', () => {
    renderPage()

    expect(screen.queryByText(/Planned/)).not.toBeInTheDocument()
    expect(screen.getByText('Voice · Phase 12')).toBeInTheDocument()
    expect(getModule('/assistant').phase).toBe(12)
  })

  it('describes the surface as a router over one classifier', () => {
    const entry = getModule('/assistant')

    expect(entry.summary).toMatch(/classifier/i)
    expect(entry.vision).toMatch(/classifier/i)
    // The placeholder promised answers with citations and locally hosted
    // inference. Neither is what Phase 12 ships, and neither may survive.
    expect(entry.vision).not.toMatch(/cites/i)
    expect(entry.vision).not.toMatch(/nothing leaves the machine/i)
    expect(entry.capabilities.map((capability) => capability.title)).not.toContain(
      'Grounded answers',
    )
  })
})
