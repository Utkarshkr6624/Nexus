/**
 * The propose → confirm pair.
 *
 * The two fixtures below are verbatim responses from a live `POST
 * /api/v1/ml/action/propose` against the running backend — one that proposed an
 * action and one that refused — because the thing this pair is easy to get wrong
 * is not the request but the answer. **A refusal is a 200.** `proposed: false`
 * with a populated `refusal` is NEXUS saying it read the sentence and would not
 * act on it, and a client that routes it into a `catch` renders a working
 * assistant as broken. So the refusal case here asserts *resolution*, not just
 * the shape of the body: `expect(...).resolves` is the test.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { confirmAction, proposeAction } from './ml'
import type { ConfirmActionRead, ConfirmActionRequest, ProposeActionRead } from '@/types/ml'

interface Recorded {
  url: string
  method: string
  body: unknown
}

/** Answers every request with `body`, recording what it was asked for. */
function stubBackend(body: unknown): { calls: Recorded[] } {
  const calls: Recorded[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method ?? 'GET',
        // The client serialises a non-native body as JSON; parsing it back is
        // what lets a test assert on the wire form rather than on the argument.
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body,
      })
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
  return { calls }
}

/** The one request the stub saw; asserts there was exactly one. */
function onlyCall(calls: Recorded[]): Recorded {
  expect(calls).toHaveLength(1)
  return calls[0] as Recorded
}

/** `POST /api/v1/ml/action/propose` → a project the user would be asked to create. */
const PROPOSAL_ANSWER: ProposeActionRead = {
  proposed: true,
  intent: 'project_manage',
  confidence: 0.984811,
  proposal: {
    kind: 'create_project',
    intent: 'project_manage',
    confidence: 0.984811,
    summary: "Create a project named 'ShapeProbeXyz'.",
    requires_confirmation: true,
    destructive: false,
    permission: 'projects.write',
    service: 'ProjectService',
    module: 'app.services.project_service',
    entrypoint: 'create',
    payload_schema: 'ProjectCreate',
    payload: {
      name: 'ShapeProbeXyz',
      description: null,
      priority: 'medium',
      start_date: null,
      target_date: null,
    },
    target_id: null,
    target_label: null,
    arguments: [
      {
        field: 'title',
        value: 'ShapeProbeXyz',
        matched_text: 'add a project called ShapeProbeXyz',
        rule: "the utterance with 'a project called' removed",
      },
    ],
    notes: [],
  },
  refusal: null,
}

/** The same route answering "no" — and answering it with a 200, as it must. */
const REFUSAL_ANSWER: ProposeActionRead = {
  proposed: false,
  intent: 'out_of_scope',
  confidence: 0.992313,
  proposal: null,
  refusal: {
    kind: null,
    intent: 'out_of_scope',
    confidence: 0.992313,
    reason_code: 'unsupported_intent',
    reason:
      "NEXUS cannot build an action from 'out_of_scope': that class names a surface to look at, not something to write.",
    arguments: [],
    notes: [],
  },
}

describe('ml action proposal', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns the proposal for an utterance NEXUS can act on', async () => {
    stubBackend(PROPOSAL_ANSWER)

    const answer = await proposeAction('add a project called ShapeProbeXyz')

    expect(answer.proposed).toBe(true)
    expect(answer.proposal?.kind).toBe('create_project')
    // The sentence a dialog renders is the one the backend composed; a client
    // that reassembled its own from `payload` would be a second, drifting
    // description of the same write.
    expect(answer.proposal?.summary).toBe("Create a project named 'ShapeProbeXyz'.")
    // The payload is free-form and published verbatim, so the confirm body can
    // be assembled from it without a second definition of what it means.
    expect(answer.proposal?.payload).toEqual({
      name: 'ShapeProbeXyz',
      description: null,
      priority: 'medium',
      start_date: null,
      target_date: null,
    })
  })

  it('sends the utterance verbatim and adds nothing the caller did not ask for', async () => {
    const { calls } = stubBackend(PROPOSAL_ANSWER)

    await proposeAction('  add a project called ShapeProbeXyz  ')

    const call = onlyCall(calls)
    expect(call.url).toContain('/ml/action/propose')
    expect(call.method).toBe('POST')
    // No trimming (the model was trained on raw strings) and no `project_id`
    // key at all, rather than an explicit `null` the backend would have to
    // re-interpret as "no project".
    expect(call.body).toEqual({ text: '  add a project called ShapeProbeXyz  ' })
  })

  it('passes the project and the zone the endpoint cannot resolve itself', async () => {
    const { calls } = stubBackend(PROPOSAL_ANSWER)

    await proposeAction('add a task to draft the plan', {
      projectId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      tz: 'Europe/Berlin',
    })

    const call = onlyCall(calls)
    expect(call.url).toContain('tz=Europe%2FBerlin')
    expect(call.body).toEqual({
      text: 'add a task to draft the plan',
      project_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    })
  })

  /**
   * The bug this pair exists to prevent, pinned from the other side.
   *
   * A refusal is an ordinary answer and must reach the caller as one. If this
   * ever starts rejecting, the assistant shows a failed turn for a sentence
   * NEXUS merely declined — which is the honest reading of "NEXUS read that and
   * would not act".
   */
  it('surfaces a refusal as a resolved answer rather than an error', async () => {
    stubBackend(REFUSAL_ANSWER)

    const answer = await proposeAction('what is the capital of France')

    expect(answer.proposed).toBe(false)
    expect(answer.proposal).toBeNull()
    expect(answer.refusal?.reason_code).toBe('unsupported_intent')
    // The classifier's own answer survives a refusal, which is what lets the
    // existing "Go to X" routing still say where the sentence lands.
    expect(answer.intent).toBe('out_of_scope')
  })
})

describe('ml action confirmation', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const CONFIRMED: ConfirmActionRead = {
    kind: 'create_project',
    entity: 'project',
    entity_id: 'a9e9ce1c-0000-4000-8000-000000000000',
    outcome: 'created',
    applied: true,
    message: "Created the project 'ShapeProbeXyz'.",
  }

  it('posts exactly the body it is given, with nothing added or renamed', async () => {
    const { calls } = stubBackend(CONFIRMED)

    const body: ConfirmActionRequest = {
      kind: 'create_project',
      intent: 'project_manage',
      payload: { name: 'ShapeProbeXyz', priority: 'medium' },
      confirm_destructive: false,
    }
    await confirmAction(body)

    const call = onlyCall(calls)
    expect(call.url).toContain('/ml/action/confirm')
    expect(call.method).toBe('POST')
    // Every field is re-checked server-side against a frozen spec, so the client
    // has nothing to add: no service pointer, no permission, no confidence.
    expect(call.body).toEqual(body)
  })

  it('carries a target through untouched, so a completion names the row it acts on', async () => {
    const { calls } = stubBackend({ ...CONFIRMED, kind: 'complete_task', entity: 'task' })

    await confirmAction({
      kind: 'complete_task',
      intent: 'task_manage',
      payload: { status: 'done' },
      confirm_destructive: false,
      target_id: 'b9e9ce1c-0000-4000-8000-000000000000',
    })

    expect(onlyCall(calls).body).toEqual({
      kind: 'complete_task',
      intent: 'task_manage',
      payload: { status: 'done' },
      confirm_destructive: false,
      target_id: 'b9e9ce1c-0000-4000-8000-000000000000',
    })
  })

  it('carries the destructive acknowledgement through untouched', async () => {
    // The endpoint refuses a destructive kind without this flag, so a client that
    // dropped or renamed it would 422 on every delete. The service is a pass-through
    // and that is the property worth pinning: whatever it is given is what is sent.
    const { calls } = stubBackend({
      ...CONFIRMED,
      kind: 'delete_task',
      entity: 'task',
      outcome: 'deleted',
      applied: true,
      message: "Deleted the task 'draft the API contract'.",
    })

    await confirmAction({
      kind: 'delete_task',
      intent: 'task_manage',
      payload: {},
      confirm_destructive: true,
      target_id: 'b9e9ce1c-0000-4000-8000-000000000000',
    })

    expect(onlyCall(calls).body).toEqual({
      kind: 'delete_task',
      intent: 'task_manage',
      payload: {},
      confirm_destructive: true,
      target_id: 'b9e9ce1c-0000-4000-8000-000000000000',
    })
  })

  it("reports what the service did in its own words, including a replay's no_op", async () => {
    stubBackend(CONFIRMED)
    await expect(
      confirmAction({
        kind: 'create_project',
        intent: 'project_manage',
        payload: { name: 'ShapeProbeXyz' },
        confirm_destructive: false,
      }),
    ).resolves.toMatchObject({ outcome: 'created', applied: true })

    stubBackend({
      ...CONFIRMED,
      outcome: 'no_op',
      applied: false,
      message: "A project named 'ShapeProbeXyz' already exists; NEXUS did not create a second one.",
    })
    const replay = await confirmAction({
      kind: 'create_project',
      intent: 'project_manage',
      payload: { name: 'ShapeProbeXyz' },
      confirm_destructive: false,
    })

    // A replayed confirm is a success-shaped answer, not a failure: the desired
    // state was reached by the first call.
    expect(replay.outcome).toBe('no_op')
    expect(replay.applied).toBe(false)
    expect(replay.entity_id).toBe(CONFIRMED.entity_id)
  })
})
