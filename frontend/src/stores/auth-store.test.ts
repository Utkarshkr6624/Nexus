import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient, ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import type { TokenPair, User } from '@/types'

/**
 * Session lifetime against a mocked backend.
 *
 * Everything here is about *ordering*: a rotation, a sign-in, a sign-out and a
 * boot verification all sit on the wire at once, and each of them is reported
 * against the session it started from rather than the one the store happens to
 * hold when the answer lands. The tests control resolution order with deferred
 * responses rather than by racing two calls, so what is asserted is the
 * sequence and not a timing accident.
 *
 * The store is module-scoped and persisted, so each test starts from an
 * explicitly anonymous session with an empty `localStorage`.
 */

/* Typed as `User` on purpose, for the same reason as in the page tests: an
 * untyped literal satisfies any fetch stub, so a wire-shape drift would show up
 * as a confusing failure somewhere else instead of here. */
const USER_A: User = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'ada@nexus.local',
  username: 'ada',
  display_name: 'Ada Lovelace',
  avatar_url: null,
  role: 'user',
  permissions: [],
  is_active: true,
  is_verified: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

const USER_B: User = {
  ...USER_A,
  id: '33333333-3333-4333-8333-333333333333',
  email: 'grace@nexus.local',
  username: 'grace',
  display_name: 'Grace Hopper',
}

const SESSION_A = 'aaaaaaaa-1111-4111-8111-111111111111'
const SESSION_B = 'bbbbbbbb-2222-4222-8222-222222222222'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function errorBody(code: string, message: string): { error: Record<string, unknown> } {
  return { error: { code, message, details: null, request_id: 'r1' } }
}

function tokenPair(access: string, refresh: string, sessionId: string): TokenPair {
  return {
    access_token: access,
    refresh_token: refresh,
    token_type: 'bearer',
    expires_in: 3600,
    session_id: sessionId,
  }
}

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
}

/** A response the test releases by hand, so "before it lands" is a state, not a race. */
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}

function signIn(access: string, refresh: string, user: User): void {
  useAuthStore.setState({
    accessToken: access,
    refreshToken: refresh,
    user,
    status: 'authenticated',
    pending: false,
    error: null,
  })
}

beforeEach(() => {
  window.localStorage.clear()
  useAuthStore.setState({
    accessToken: null,
    refreshToken: null,
    user: null,
    status: 'anonymous',
    pending: false,
    error: null,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('a rotation that lands after the session moved on', () => {
  /**
   * The control for the two tests below: a rotation whose session is still the
   * one the store holds commits. Without it, a mock that quietly failed the
   * refresh would make the "superseded" expectations below pass for the wrong
   * reason — a rejection is superseded too.
   */
  it('commits the renewal when the session it started from is still the one held', async () => {
    signIn('a0', 'r0', USER_A)

    let refreshCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) {
          refreshCalls += 1
          return json(tokenPair('a1', 'r1', SESSION_A))
        }
        return json(errorBody('not_found', 'Not found'), 404)
      }),
    )

    expect(await useAuthStore.getState().recoverSession()).toEqual({
      kind: 'renewed',
      token: 'a1',
    })
    expect(refreshCalls).toBe(1)
    expect(useAuthStore.getState().accessToken).toBe('a1')
    expect(useAuthStore.getState().refreshToken).toBe('r1')
    expect(useAuthStore.getState().status).toBe('authenticated')
  })

  it('does not resurrect a session that was signed out while it was in flight', async () => {
    signIn('a0', 'r0', USER_A)

    const rotation = deferred<Response>()
    let refreshCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) {
          refreshCalls += 1
          return rotation.promise
        }
        if (url.includes('/auth/logout')) return new Response(null, { status: 204 })
        return json(errorBody('not_found', 'Not found'), 404)
      }),
    )

    const recovering = useAuthStore.getState().recoverSession()
    await vi.waitFor(() => expect(refreshCalls).toBe(1))

    // The user signs out while the rotation is still on the wire.
    await useAuthStore.getState().logout()
    expect(useAuthStore.getState().status).toBe('anonymous')

    rotation.resolve(json(tokenPair('a1', 'r1', SESSION_A)))

    // The rotation itself worked; it simply belongs to a session that is over.
    expect(await recovering).toEqual({ kind: 'superseded' })

    const state = useAuthStore.getState()
    // The bug this guards: committing the result would leave an `anonymous`
    // status still carrying a bearer — signed out on screen, authenticated on
    // the wire.
    expect(state.status).toBe('anonymous')
    expect(state.accessToken).toBeNull()
    expect(state.refreshToken).toBeNull()
    expect(state.user).toBeNull()
  })

  it('does not overwrite a newer sign-in with the rotation it superseded', async () => {
    signIn('a-access', 'a-refresh', USER_A)

    const rotation = deferred<Response>()
    let refreshCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) {
          refreshCalls += 1
          return rotation.promise
        }
        if (url.includes('/auth/login')) return json(tokenPair('b-access', 'b-refresh', SESSION_B))
        if (url.includes('/auth/me')) return json(USER_B)
        return json(errorBody('not_found', 'Not found'), 404)
      }),
    )

    const recovering = useAuthStore.getState().recoverSession()
    await vi.waitFor(() => expect(refreshCalls).toBe(1))

    // A different account signs in on the same store while A's rotation is out.
    await useAuthStore.getState().login({ email: USER_B.email, password: 'irrelevant' })
    expect(useAuthStore.getState().accessToken).toBe('b-access')

    rotation.resolve(json(tokenPair('a-access-2', 'a-refresh-2', SESSION_A)))

    expect(await recovering).toEqual({ kind: 'superseded' })

    // The cross-account bug: A's tokens on B's shell. `user` matters as much
    // as the token — the whole interface is labelled from it.
    const state = useAuthStore.getState()
    expect(state.accessToken).toBe('b-access')
    expect(state.refreshToken).toBe('b-refresh')
    expect(state.user?.id).toBe(USER_B.id)
    expect(state.user?.email).toBe(USER_B.email)
    expect(state.status).toBe('authenticated')
  })
})

describe('boot verification of a persisted session', () => {
  /**
   * Drives `hydrate()` against a backend that answers `/auth/me` with a
   * non-401, one attempt at a time.
   *
   * The gates are released by hand so each intermediate store state is observed
   * deliberately rather than inferred from a sleep. A 500, a 429 and a 404 from
   * a wrong base URL all say the same thing: the backend did not tell us the
   * token is bad. None of them may be read as a verdict, and none of them may
   * leave a live bearer attached to a shell that has given up on it.
   */
  async function driveBootVerification(status: number): Promise<void> {
    const gates: Array<Deferred<Response>> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (!url.includes('/auth/me')) return json(errorBody('not_found', 'Not found'), 404)
        const gate = deferred<Response>()
        gates.push(gate)
        return gate.promise
      }),
    )

    // The shape a reload restores: a pair and a user, with `status` recomputed
    // by `hydrate` rather than persisted.
    useAuthStore.setState({
      accessToken: 'boot-access',
      refreshToken: 'boot-refresh',
      user: USER_A,
      status: 'initializing',
      pending: false,
      error: null,
    })

    const hydrating = useAuthStore.getState().hydrate()
    await vi.waitFor(() => expect(gates).toHaveLength(1))

    // The first attempt has not answered yet, so no verdict has been reached.
    expect(useAuthStore.getState().status).toBe('initializing')

    for (let attempt = 0; attempt < 3; attempt += 1) {
      const gate = gates[attempt] as Deferred<Response>
      gate.resolve(json(errorBody('internal_error', 'Backend is unhappy'), status))

      if (attempt === 2) break
      // The store buys another attempt, so the next one is on the wire.
      await vi.waitFor(() => expect(gates).toHaveLength(attempt + 2), { timeout: 3_000 })

      // The regression, asserted while the budget is still unspent: a backend
      // that did not answer must never read as "signed out" with the bearer
      // still attached to every request.
      const mid = useAuthStore.getState()
      expect(mid.status).toBe('initializing')
      expect(mid.accessToken).toBe('boot-access')
      expect(mid.refreshToken).toBe('boot-refresh')
      expect(mid.user?.id).toBe(USER_A.id)
    }

    await hydrating

    // Budget spent, backend still silent: the only place a non-401 is allowed
    // to end the session, and it ends all of it.
    const settled = useAuthStore.getState()
    expect(settled.status).toBe('anonymous')
    expect(settled.accessToken).toBeNull()
    expect(settled.refreshToken).toBeNull()
    expect(settled.user).toBeNull()
  }

  it('holds an unverified session unverified on a 500 rather than half-signing it out', async () => {
    await driveBootVerification(500)
  })

  it('treats a 429 as no verdict either', async () => {
    await driveBootVerification(429)
  })

  it('treats a 404 from a wrong base URL as no verdict', async () => {
    await driveBootVerification(404)
  })
})

describe('a persisted session whose access token has expired', () => {
  /**
   * The bug this guards is a hang, not a wrong verdict: the boot check reached
   * 'superseded' — because the API client's own 401 recovery rotated the pair
   * out from under the guard `loadUser` was holding — and 'superseded' ends the
   * check without deciding anything. Nothing then set a status, so both route
   * guards rendered "Restoring session" for the rest of the session's life.
   */
  it('renews it and lands on authenticated instead of stalling on the boot screen', async () => {
    useAuthStore.setState({
      accessToken: 'expired-access',
      refreshToken: 'expired-refresh',
      user: USER_A,
      status: 'initializing',
      pending: false,
      error: null,
    })

    const presented: string[] = []
    let refreshCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) {
          refreshCalls += 1
          return json(tokenPair('fresh-access', 'fresh-refresh', SESSION_A))
        }
        if (url.includes('/auth/me')) {
          const bearer = new Headers(init?.headers).get('Authorization') ?? ''
          presented.push(bearer)
          // Only the stored token is spent; the renewed one identifies the user.
          return bearer === 'Bearer fresh-access'
            ? json(USER_A)
            : json(errorBody('unauthorized', 'Not authenticated'), 401)
        }
        return json(errorBody('not_found', 'Not found'), 404)
      }),
    )

    await useAuthStore.getState().hydrate()

    expect(presented).toEqual(['Bearer expired-access', 'Bearer fresh-access'])
    // One single-use refresh token spent, not one per attempt.
    expect(refreshCalls).toBe(1)

    const state = useAuthStore.getState()
    expect(state.status).toBe('authenticated')
    expect(state.accessToken).toBe('fresh-access')
    expect(state.refreshToken).toBe('fresh-refresh')
    expect(state.user?.id).toBe(USER_A.id)
    expect(state.pending).toBe(false)
  })
})

describe('a sign-in whose /auth/me is refused', () => {
  /**
   * A token the backend has just minted can still arrive spent — a race, a
   * proxy in the middle, a clock that disagrees. The client's recovery renews
   * it and replays the request, and that renewal is the same session with newer
   * tokens, so the store adopts the account it got back rather than reporting a
   * superseded sign-in and leaving a fresh pair on an anonymous store.
   */
  function stubRenewingBackend(): () => number {
    let refreshCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) {
          refreshCalls += 1
          return json(tokenPair('a2', 'r2', SESSION_A))
        }
        if (url.includes('/auth/login')) return json(tokenPair('a1', 'r1', SESSION_A))
        if (url.includes('/auth/register')) return json(USER_A, 201)
        if (url.includes('/auth/me')) {
          const bearer = new Headers(init?.headers).get('Authorization') ?? ''
          return bearer === 'Bearer a2'
            ? json(USER_A)
            : json(errorBody('unauthorized', 'Not authenticated'), 401)
        }
        return json(errorBody('not_found', 'Not found'), 404)
      }),
    )
    return () => refreshCalls
  }

  it('signs the user in after the client renews the token it just minted', async () => {
    const refreshCalls = stubRenewingBackend()

    await useAuthStore.getState().login({ email: USER_A.email, password: 'irrelevant' })

    expect(refreshCalls()).toBe(1)
    const state = useAuthStore.getState()
    expect(state.status).toBe('authenticated')
    expect(state.pending).toBe(false)
    expect(state.error).toBeNull()
    expect(state.accessToken).toBe('a2')
    expect(state.refreshToken).toBe('r2')
    expect(state.user?.id).toBe(USER_A.id)
  })

  it('signs the user in the same way when the account was just created', async () => {
    const refreshCalls = stubRenewingBackend()

    await useAuthStore.getState().register({
      email: USER_A.email,
      username: USER_A.username,
      password: 'irrelevant',
    })

    expect(refreshCalls()).toBe(1)
    const state = useAuthStore.getState()
    expect(state.status).toBe('authenticated')
    // The create-account form's spinner is released the same way: nothing about
    // the renewal may leave it on "Creating account…".
    expect(state.pending).toBe(false)
    expect(state.error).toBeNull()
    expect(state.accessToken).toBe('a2')
    expect(state.user?.id).toBe(USER_A.id)
  })

  it('reports the failure and releases the form when the request fails after a renewal', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) return json(tokenPair('a2', 'r2', SESSION_A))
        if (url.includes('/auth/login')) return json(tokenPair('a1', 'r1', SESSION_A))
        if (url.includes('/auth/me')) {
          const bearer = new Headers(init?.headers).get('Authorization') ?? ''
          if (bearer === 'Bearer a2') {
            return json(errorBody('internal_error', 'Backend is unhappy'), 500)
          }
          return json(errorBody('unauthorized', 'Not authenticated'), 401)
        }
        return json(errorBody('not_found', 'Not found'), 404)
      }),
    )

    await useAuthStore.getState().login({ email: USER_A.email, password: 'irrelevant' })

    const state = useAuthStore.getState()
    // The renewal replaced the refresh token this call claimed, so the guard
    // that used to skip the whole catch body saw a pair that was not its own
    // and reported nothing at all — no message and a permanently disabled form.
    expect(state.error).not.toBeNull()
    expect(state.pending).toBe(false)
    // The renewed pair never became a session, so it does not survive either.
    expect(state.status).toBe('anonymous')
    expect(state.accessToken).toBeNull()
    expect(state.refreshToken).toBeNull()
  })
})

describe('storage that refuses to be written', () => {
  /**
   * zustand guards the storage *accessor*, not the writes: a `setItem` that
   * throws (quota, private browsing) made every store write a rejected promise,
   * which showed up as a sign-in stuck on "Signing in…", a sign-out that never
   * navigated, and a boot screen that never left. Persistence is lost; nothing
   * else may be.
   */
  it('degrades to an in-memory session rather than rejecting the actions', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/login')) return json(tokenPair('a1', 'r1', SESSION_A))
        if (url.includes('/auth/me')) return json(USER_A)
        if (url.includes('/auth/logout')) return new Response(null, { status: 204 })
        return json(errorBody('not_found', 'Not found'), 404)
      }),
    )

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('The quota has been exceeded.', 'QuotaExceededError')
    })

    await useAuthStore.getState().login({ email: USER_A.email, password: 'irrelevant' })

    expect(setItem).toHaveBeenCalled()
    const signedIn = useAuthStore.getState()
    expect(signedIn.status).toBe('authenticated')
    expect(signedIn.pending).toBe(false)
    expect(signedIn.user?.id).toBe(USER_A.id)

    // And the sign-out that a rejected write used to swallow whole, leaving the
    // user on a signed-in shell that no longer matches the backend.
    await expect(useAuthStore.getState().logout()).resolves.toBeUndefined()
    expect(useAuthStore.getState().status).toBe('anonymous')
  })

  it('still reaches a verdict at boot, rather than pinning the app on the boot screen', async () => {
    // The shape a reload restores: a pair and a user, with `status` recomputed
    // by `hydrate` rather than persisted.
    useAuthStore.setState({
      accessToken: 'stored-access',
      refreshToken: 'stored-refresh',
      user: USER_A,
      status: 'initializing',
      pending: false,
      error: null,
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('The quota has been exceeded.', 'QuotaExceededError')
    })

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/me')) return json(USER_A)
        return json(errorBody('not_found', 'Not found'), 404)
      }),
    )

    await expect(useAuthStore.getState().hydrate()).resolves.toBeUndefined()

    const state = useAuthStore.getState()
    expect(state.status).toBe('authenticated')
    expect(state.user?.id).toBe(USER_A.id)
  })
})

describe('401 recovery', () => {
  it('ends the session once two consecutive requests stay 401 after a renewal', async () => {
    signIn('a0', 'r0', USER_A)

    let refreshCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) {
          refreshCalls += 1
          return json(tokenPair('a1', 'r1', SESSION_A))
        }
        // An endpoint that refuses every bearer the store mints.
        if (url.includes('/projects/ok')) return json({ ok: true })
        return json(errorBody('unauthorized', 'Not authenticated'), 401)
      }),
    )

    // A request that is not refused breaks the streak, so the count below
    // measures consecutive failures and not lifetime totals.
    await expect(apiClient.get('/projects/ok')).resolves.toEqual({ ok: true })

    await expect(apiClient.get('/projects')).rejects.toBeInstanceOf(ApiError)
    // One post-renewal 401 is the ordinary case of a token spent mid-flight:
    // the renewal that did succeed is committed, and the session survives.
    expect(useAuthStore.getState().status).toBe('authenticated')
    expect(useAuthStore.getState().accessToken).toBe('a1')
    expect(useAuthStore.getState().refreshToken).toBe('r1')

    await expect(apiClient.get('/projects')).rejects.toBeInstanceOf(ApiError)
    // Two is the verdict: rotating again would spend a single-use refresh token
    // per request and never resolve.
    expect(useAuthStore.getState().status).toBe('anonymous')
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().refreshToken).toBeNull()

    const spent = refreshCalls
    expect(spent).toBe(2)

    // And it stays ended: the client stops rotating rather than looping.
    await expect(apiClient.get('/projects')).rejects.toBeInstanceOf(ApiError)
    expect(refreshCalls).toBe(spent)
    expect(useAuthStore.getState().status).toBe('anonymous')
  })

  it('surfaces the transport failure from the renewal, not the 401 that triggered it', async () => {
    signIn('a0', 'r0', USER_A)

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) throw new TypeError('Failed to fetch')
        return json(errorBody('unauthorized', 'Not authenticated'), 401)
      }),
    )

    const thrown = await apiClient.get('/projects').catch((cause: unknown) => cause)

    expect(thrown).toBeInstanceOf(ApiError)
    const error = thrown as ApiError
    // A 401 here would be a claim the server never made: it never answered.
    expect(error.status).not.toBe(401)
    expect(error.isTransportError).toBe(true)
    expect(error.code).toBe('network_error')

    // Which also means the stored pair is no verdict either — a network blip
    // must not end the session.
    expect(useAuthStore.getState().status).toBe('authenticated')
    expect(useAuthStore.getState().accessToken).toBe('a0')
    expect(useAuthStore.getState().refreshToken).toBe('r0')
  })
})
