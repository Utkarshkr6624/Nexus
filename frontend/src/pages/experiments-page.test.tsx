import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ExperimentsPage from '@/pages/experiments-page'

/**
 * The Experiments page, asserted against what it is allowed to do.
 *
 * Experiments has no backend — no path in the OpenAPI document, no router,
 * model, repository or table, and a 404 from a running server. This suite
 * exists to pin that fact to the page, because the page's honesty is the only
 * thing standing between an empty module and a plausible-looking lie.
 *
 * The risk it guards against is specific and was the original complaint: a
 * surface where the obvious "just add a button" fix produces a control that
 * saves nothing, or one that keeps records in browser storage where no other
 * device can see them. Both are asserted against below rather than merely left
 * unwritten, so a later attempt to close the gap has to argue with a test
 * instead of slipping past one.
 */

function renderExperiments() {
  return render(<ExperimentsPage />)
}

describe('experiments page', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('says outright that an experiment cannot be recorded, and why', () => {
    renderExperiments()

    expect(screen.getByRole('heading', { level: 1, name: 'Experiments' })).toBeInTheDocument()
    expect(screen.getByText('Planned — Phase 10')).toBeInTheDocument()

    // The answer to "why is there no way to record one?" is on the page, in
    // words, before the reader has to go looking for it.
    const notice = screen.getByRole('alert')
    expect(notice).toHaveTextContent(
      'There is no way to record an experiment yet, and that is deliberate.',
    )
    expect(notice).toHaveTextContent('The experiments module has no backend.')
    expect(notice).toHaveTextContent('no experiment endpoint to create, read or update against')
  })

  it('offers no control that would look like it saved something', () => {
    renderExperiments()

    // Not "the button is disabled" — there is no button, because a control with
    // nowhere to submit to is a promise the product cannot keep.
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.queryAllByRole('link')).toHaveLength(0)
    expect(screen.queryAllByRole('textbox')).toHaveLength(0)
    expect(screen.queryAllByRole('combobox')).toHaveLength(0)
    expect(screen.queryByRole('form')).toBeNull()
  })

  it('asks the network for nothing, because there is no experiments endpoint', () => {
    const fetchStub = vi.fn(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchStub)

    renderExperiments()

    // The whole finding, asserted structurally: a page for a module with no
    // backend must not go looking for one. If a service layer ever lands here,
    // this test is where that decision becomes visible and has to be justified.
    expect(fetchStub).not.toHaveBeenCalled()
  })

  it('keeps no records in browser storage', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')

    renderExperiments()

    // Local storage is not a stand-in for a record. An experiment kept only in
    // one browser would be invisible to every other device and uncountable by
    // every report, while looking identical on screen to one that was saved.
    expect(setItem).not.toHaveBeenCalled()
  })

  it('drops the placeholder tiles instead of showing an em dash for data it has no way to get', () => {
    renderExperiments()

    // "—" is the universal sign for a value that is temporarily unavailable,
    // which reads as a failed load. Nothing here has ever been loadable, so the
    // tiles are gone rather than blank. Matched as a whole element, because the
    // badge legitimately reads "Planned — Phase 10" and must keep doing so.
    expect(screen.queryAllByText('—')).toHaveLength(0)
    expect(screen.queryByText('Requires experiment records')).toBeNull()
    expect(screen.queryByText('Requires recorded verdicts')).toBeNull()
    expect(screen.queryByText('Running experiments')).toBeNull()
  })

  it('names the empty state in plain language rather than pasting the module name into it', () => {
    renderExperiments()

    // "No Experiments records yet" put the nav label into a sentence, where it
    // read as a typo and left a noun without a verb.
    expect(screen.getByText('No experiments recorded yet')).toBeInTheDocument()
    expect(screen.queryByText('No Experiments records yet')).toBeNull()
  })

  it('still explains what the module is for, so the page is not a dead end', () => {
    renderExperiments()

    expect(screen.getByRole('heading', { name: 'What experiments will do' })).toBeInTheDocument()
    // The roadmap is the part that is actually true today, and it stays.
    for (const capability of ['Hypothesis and metric', 'Bounded scope', 'Keep or kill']) {
      expect(screen.getByText(capability)).toBeInTheDocument()
    }
  })
})