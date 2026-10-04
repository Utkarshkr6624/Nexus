import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiError, queryFrom } from '@/lib/api-client'

/**
 * `fetch` is always stubbed — these tests must never touch the network. The
 * client resolves `globalThis.fetch` at call time, so stubbing after
 * construction is enough.
 */
afterEach(() => {
  vi.unstubAllGlobals()
})

function client(): ApiClient {
  return new ApiClient({ baseUrl: '/api/v1' })
}

function respond(body: unknown, status: number, headers: Record<string, string> = {}): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(typeof body === 'string' ? body : JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json', ...headers },
        }),
    ),
  )
}

describe('ApiError mapping', () => {
  it('maps the backend error envelope onto code, message, details and requestId', async () => {
    respond(
      {
        error: {
          code: 'validation_error',
          message: 'Email is not a valid address',
          details: { email: ['value is not a valid email address'] },
          request_id: 'b1f4c0de-0000-4000-8000-000000000001',
        },
      },
      422,
    )

    const error = await client()
      .get('/auth/login')
      .catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.code).toBe('validation_error')
    expect(apiError.message).toBe('Email is not a valid address')
    expect(apiError.details).toEqual({ email: ['value is not a valid email address'] })
    expect(apiError.requestId).toBe('b1f4c0de-0000-4000-8000-000000000001')
    expect(apiError.status).toBe(422)
    expect(apiError.isValidationError).toBe(true)
    expect(apiError.isTransportError).toBe(false)
    expect(apiError.fieldErrors).toEqual({ email: ['value is not a valid email address'] })
  })

  it('maps a network failure to the transport error, never to an HTTP status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    const error = await client()
      .get('/health')
      .catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.code).toBe('network_error')
    expect(apiError.status).toBe(0)
    expect(apiError.isTransportError).toBe(true)
    expect(apiError.message).toBe('Failed to fetch')
    // A transport failure has no server-side request id to quote.
    expect(apiError.requestId).toBeNull()
  })

  it('falls back to the status when the body is not our envelope, keeping the header id', async () => {
    respond('<html>502 Bad Gateway</html>', 502, { 'x-request-id': 'proxy-req-77' })

    const error = await client()
      .get('/health')
      .catch((cause: unknown) => cause)

    const apiError = error as ApiError
    expect(apiError.status).toBe(502)
    expect(apiError.code).toBe('internal_error')
    expect(apiError.message).toBe('<html>502 Bad Gateway</html>')
    expect(apiError.requestId).toBe('proxy-req-77')
  })
})
describe('the request timeout', () => {
  /**
   * `fetch` resolves on headers, so a server that flushes them and then stalls
   * the body used to leave the promise pending forever: no `ApiError`, and
   * therefore no React Query retry. The abort and its timer have to outlive the
   * headers, which means outliving the `response.text()` calls that follow.
   */
  it('covers reading the body, not just waiting for the headers', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            // Headers and part of a body, then nothing — the shape of a stall.
            controller.enqueue(new TextEncoder().encode('{"id":'))
            init?.signal?.addEventListener('abort', () => {
              controller.error(new DOMException('The operation was aborted.', 'AbortError'))
            })
          },
        })
        return new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }),
    )

    const error = await new ApiClient({ baseUrl: '/api/v1', timeoutMs: 25 })
      .get('/stalled')
      .catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.isTimeout).toBe(true)
    // No HTTP status was ever completed, so this is not a response the backend
    // would have to be asked about again — but it is a thrown error, which is
    // what lets the retry happen at all.
    expect(apiError.isTransportError).toBe(true)
  })

  it('leaves a body that arrives in time alone', async () => {
    respond({ ok: true }, 200)

    await expect(new ApiClient({ baseUrl: '/api/v1', timeoutMs: 25 }).get('/quick')).resolves.toEqual({
      ok: true,
    })
  })
})

describe('recoverOn401', () => {
  /**
   * The opt-out for a caller that runs its own renewal: letting the client
   * rotate a token mid-call replaces the very pair the caller is guarding its
   * write on.
   */
  it('surfaces the 401 instead of renewing when recovery is off', async () => {
    const renewals = { calls: 0 }
    const instance = new ApiClient({
      baseUrl: '/api/v1',
      getToken: () => 'spent-access',
    })
    instance.setUnauthorizedHandler(async () => {
      renewals.calls += 1
      return { kind: 'renewed', token: 'fresh-access' }
    })

    respond(errorEnvelope(), 401)

    const error = await instance
      .get('/auth/me', { recoverOn401: false })
      .catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(401)
    expect(renewals.calls).toBe(0)
  })

  it('renews and replays once when recovery is on, the default', async () => {
    const renewals = { calls: 0 }
    const instance = new ApiClient({
      baseUrl: '/api/v1',
      getToken: () => 'spent-access',
    })
    instance.setUnauthorizedHandler(async () => {
      renewals.calls += 1
      return { kind: 'renewed', token: 'fresh-access' }
    })

    respond(errorEnvelope(), 401)
    await expect(instance.get('/auth/me')).rejects.toBeInstanceOf(ApiError)
    expect(renewals.calls).toBe(1)
  })
})

function errorEnvelope(): { error: Record<string, unknown> } {
  return { error: { code: 'unauthorized', message: 'Not authenticated', details: null } }
}

describe('queryFrom', () => {
  /**
   * The one params filter, shared by every service module. What it drops is the
   * whole point of it, so the dropped cases are asserted individually rather
   * than through a round trip: a `?search=` that slips through asks the backend
   * for the empty search and comes back with an empty result set that looks
   * exactly like a search which legitimately found nothing.
   */

  it('keeps the values the client can serialise', () => {
    expect(queryFrom({ limit: 25, offset: 0, search: 'notes', archived: false })).toEqual({
      limit: 25,
      offset: 0,
      search: 'notes',
      archived: false,
    })
  })

  it.each([
    ['undefined', { limit: undefined }],
    ['null', { project_id: null }],
    ['the empty string', { search: '' }],
  ])('drops %s rather than serialising it as a literal', (_label, params) => {
    expect(queryFrom(params)).toEqual({})
  })

  it('drops an array, which is not one query value', () => {
    // A repeated list is spelled on the path by `withRepeatedParam`, not here:
    // the client can only emit a key once.
    expect(queryFrom({ tag_ids: ['a', 'b'], limit: 10 })).toEqual({ limit: 10 })
  })

  it('keeps a zero, which is a measurement and not an absence', () => {
    expect(queryFrom({ offset: 0, confidence: 0 })).toEqual({ offset: 0, confidence: 0 })
  })

  it('accepts a typed params interface, not just a record', () => {
    interface ListParams {
      limit?: number
      search?: string
    }
    const params: ListParams = { limit: 5 }
    expect(queryFrom(params)).toEqual({ limit: 5 })
  })
})
