import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ConversationLog } from '@/features/assistant/components/conversation-log'
import type { VoiceTurn } from '@/features/assistant/types'
import type { RoutingDecisionRead } from '@/types/ml'

/**
 * What the assistant did with what it heard.
 *
 * **These assertions are about language, because the language is the feature.**
 * A classifier answers with an intent, a confidence and a service name; a panel
 * that dresses that as a reply has invented a product NEXO does not have. So the
 * tests check that a routed turn reads as a *decision* naming a real call, and
 * that the two outcomes which are not "here is your thing" — the one needing
 * generation and the one with no surface — say so plainly instead of apologising
 * or improvising.
 *
 * The intents are checked by name rather than by payload: `RoutingDecisionRead`
 * is frozen at nine fields and carries no label, so the label is a decision this
 * component makes, and a decision worth making is worth asserting.
 */

const AT = Date.parse('2026-03-04T09:30:00Z')

function decision(overrides: Partial<RoutingDecisionRead> = {}): RoutingDecisionRead {
  return {
    intent: 'task_manage',
    confidence: 0.97,
    threshold: 0.62,
    status: 'accepted',
    destination: 'api/v1/tasks',
    destination_kind: 'router',
    target: {
      service: 'TaskService',
      module: 'app.services.task_service',
      entrypoint: 'TaskService.list',
    },
    reason: 'Task management is a validated NEXUS destination.',
    alternatives: [],
    ...overrides,
  }
}

function turn(overrides: Partial<VoiceTurn> = {}): VoiceTurn {
  return {
    id: 'turn-1',
    transcript: 'show me my open tasks',
    decision: decision(),
    error: null,
    at: AT,
    ...overrides,
  }
}

function renderOutcome(payload: RoutingDecisionRead) {
  const { container } = render(
    <ol>
      <li>
        <ConversationLog turns={[turn({ decision: payload })]} />
      </li>
    </ol>,
  )
  return container
}

describe('ConversationLog', () => {
  it('explains an empty log rather than showing a blank panel', () => {
    render(<ConversationLog turns={[]} />)

    expect(screen.getByText('No requests yet')).toBeInTheDocument()
    // The privacy claim is only credible if the reader can see it is true.
    expect(screen.getByText(/stays in your browser/i)).toBeInTheDocument()
  })

  it('renders an accepted turn as a decision naming the call it would make', () => {
    render(<ConversationLog turns={[turn()]} />)

    expect(screen.getByText('show me my open tasks')).toBeInTheDocument()
    expect(screen.getByText('Show my tasks')).toBeInTheDocument()
    expect(screen.getByText(/97% confident/)).toBeInTheDocument()
    expect(screen.getByText('TaskService.list')).toBeInTheDocument()
    expect(screen.getByText('Routed')).toBeInTheDocument()
    expect(
      screen.getByText('Task management is a validated NEXUS destination.'),
    ).toBeInTheDocument()
  })

  it('never phrases a decision as a reply', () => {
    const container = renderOutcome(decision())
    const text = container.textContent ?? ''

    // Nothing behind POST /ml/route can write prose. If this assertion ever
    // fails, the panel has started claiming a capability the product lacks.
    expect(text).not.toMatch(/here (is|are)|here's|let me|I (found|think|can help)/i)
  })

  it('states plainly that a generation request has nowhere to go here', () => {
    render(
      <ConversationLog
        turns={[
          turn({
            decision: decision({
              intent: 'code_assist',
              confidence: 0.91,
              status: 'generation_unavailable',
              destination: 'large-model:unavailable',
              destination_kind: 'large_model',
              target: null,
              reason: 'Code assistance requires a generative model.',
            }),
          }),
        ]}
      />,
    )

    // The recognition is correct and is shown as such: `intent` alone would read
    // a success with nowhere to send it as a failure.
    expect(screen.getByText('Write or explain code')).toBeInTheDocument()
    expect(screen.getByText('Needs generation')).toBeInTheDocument()
    expect(screen.getByText(/no generative model/i)).toBeInTheDocument()
    expect(screen.getByText(/fourteen-class/i)).toBeInTheDocument()
    expect(screen.getByText(/Code assistance and deep reasoning/)).toBeInTheDocument()
    expect(screen.queryByText('Routed')).not.toBeInTheDocument()
  })

  it('names the surfaces NEXO does have when a request has no surface at all', () => {
    render(
      <ConversationLog
        turns={[
          turn({
            transcript: 'order me a pizza',
            decision: decision({
              intent: 'out_of_scope',
              confidence: 0.95,
              status: 'out_of_scope',
              destination: 'abstain',
              destination_kind: 'fallback',
              target: null,
              reason: 'The utterance maps to no NEXUS surface.',
            }),
          }),
        ]}
      />,
    )

    expect(screen.getByText('Out of scope')).toBeInTheDocument()
    expect(screen.getByText(/NEXUS has no surface for this/i)).toBeInTheDocument()
    expect(screen.getByText(/tasks and projects/i)).toBeInTheDocument()
    expect(screen.queryByText(/Outside NEXO/)).not.toBeInTheDocument()
  })

  it('explains an abstention with the threshold and the runners-up', () => {
    render(
      <ConversationLog
        turns={[
          turn({
            decision: decision({
              intent: 'task_manage',
              confidence: 0.41,
              status: 'uncertain',
              destination: 'abstain',
              destination_kind: 'fallback',
              target: null,
              alternatives: [
                { intent: 'analytics_insight', confidence: 0.22 },
                { intent: 'knowledge_lookup', confidence: 0.11 },
              ],
            }),
          }),
        ]}
      />,
    )

    expect(screen.getByText('Not confident enough')).toBeInTheDocument()
    expect(screen.getByText(/41% confident/)).toBeInTheDocument()
    // The number that makes the abstention arguable is the threshold, not the
    // score: it is what the next request has to beat.
    expect(screen.getByText(/below the 62%/)).toBeInTheDocument()
    expect(screen.getByText(/Also considered:/)).toBeInTheDocument()
    expect(screen.getByText(/Analyse my work data \(22%\)/)).toBeInTheDocument()
    expect(screen.queryByText('TaskService.list')).not.toBeInTheDocument()
  })

  it('shows the failure in full, and only offers a retry when one could work', () => {
    render(
      <ConversationLog
        turns={[
          turn({
            transcript: '',
            decision: null,
            error: {
              code: 'no_speech',
              message: 'Nothing speech-like arrived before the deadline.',
              retryable: true,
            },
          }),
        ]}
      />,
    )

    expect(
      screen.getByText(/Nothing speech-like arrived before the deadline\./),
    ).toBeInTheDocument()
    expect(screen.getByText(/Ask again when you are ready/)).toBeInTheDocument()
  })

  it('does not claim a turn can be repeated when it cannot', () => {
    render(
      <ConversationLog
        turns={[
          turn({
            transcript: 'show me my open tasks',
            decision: null,
            error: {
              code: 'permission_denied',
              message: 'Microphone access is blocked for this site.',
              retryable: false,
            },
          }),
        ]}
      />,
    )

    expect(screen.getByText('Microphone access is blocked for this site.')).toBeInTheDocument()
    expect(screen.queryByText(/Ask again when you are ready/)).not.toBeInTheDocument()
  })

  it('keeps every turn of the session, oldest first', () => {
    render(
      <ConversationLog
        turns={[
          turn({ id: 'a', transcript: 'first request' }),
          turn({
            id: 'b',
            transcript: 'second request',
            decision: decision({ intent: 'knowledge_lookup', confidence: 0.88 }),
          }),
        ]}
      />,
    )

    const entries = within(
      screen.getByRole('list', { name: 'Requests in this session' }),
    ).getAllByRole('listitem')
    expect(entries).toHaveLength(2)
    expect(entries[0]).toHaveTextContent('first request')
    expect(entries[1]).toHaveTextContent('second request')
  })

  it('says so when a turn completed without hearing anything', () => {
    render(<ConversationLog turns={[turn({ transcript: '   ', decision: null })]} />)
    expect(screen.getByText('Nothing was heard.')).toBeInTheDocument()
  })
})
