import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { StateStorage } from 'zustand/middleware'

import { ApiError, apiClient } from '@/lib/api-client'
import type { TokenRecovery } from '@/lib/api-client'
import {
  changePasswordRequest,
  fetchCurrentUser,
  loginRequest,
  logoutAllRequest,
  logoutRequest,
  refreshRequest,
  registerRequest,
} from '@/services/auth'
import { toApiError } from '@/services/errors'
import { deleteAccountRequest, updateProfileRequest } from '@/services/users'
import type {
  LoginPayload,
  PasswordChangePayload,
  ProfileUpdatePayload,
  RegisterPayload,
  TokenPair,
  User,
  UserDeletionPayload,
} from '@/types'

export const AUTH_STORAGE_KEY = 'nexus.auth'

/**
 * `initializing` covers the window where a persisted session is being verified
 * against the backend. Guards use it to avoid bouncing a signed-in user to
 * /login on every reload. It also covers a verification the backend never
 * answered: "unverified" is not "rejected", so the store stays in this state
 * and retries within a bounded budget rather than claiming the user is
 * anonymous while their tokens are still attached to every request.
 */
export type AuthStatus = 'initializing' | 'authenticated' | 'anonymous'

const ANONYMOUS = {
  accessToken: null,
  refreshToken: null,
  user: null,
  status: 'anonymous',
} as const

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  user: User | null
  status: AuthStatus
  /** A credential or account action is in flight. */
  pending: boolean
  /** Last auth failure, rendered inline by the form that triggered it. */
  error: ApiError | null
  login: (payload: LoginPayload) => Promise<void>
  register: (payload: RegisterPayload) => Promise<void>
  logout: () => Promise<void>
  /**
   * Revokes every *other* device's session. The endpoint spares the caller's
   * own, but the local session is ended regardless: the action's promise is
   * "one device left, starting clean", and a store still holding a valid pair
   * would render a signed-in shell that shows none of the other sessions it
   * just revoked. The change is announced, so the query cache goes with it.
   */
  logoutAll: () => Promise<void>
  /**
   * The backend revokes every session but the caller's as part of this call, so
   * the tokens already held stay valid and the user is not bounced to /login.
   */
  changePassword: (payload: PasswordChangePayload) => Promise<void>
  /**
   * Edits the caller's own profile. The response replaces `user` wholesale so
   * the avatar, name and role chip all update from one round trip.
   */
  updateProfile: (payload: ProfileUpdatePayload) => Promise<void>
  /** Destroys the account. Unlike the other actions this always ends the session. */
  deleteAccount: (payload: UserDeletionPayload) => Promise<void>
  /**
   * Verifies a persisted session against `GET /auth/me`. Single-flight: the
   * boot effect and any later caller share one verification, so a StrictMode
   * double-mount cannot race the single-use refresh token.
   */
  hydrate: () => Promise<void>
  /**
   * Renews an expired session on behalf of the API client, as the outcome the
   * client replays from. Only a refresh token the backend rejects ends the
   * session; a backend we could not reach leaves the stored pair in place and
   * says so, and a session that was signed out or replaced while the rotation
   * was in flight is reported as superseded rather than renewed.
   */
  recoverSession: () => Promise<TokenRecovery>
  clearError: () => void
}

type SessionListener = () => void

const sessionListeners = new Set<SessionListener>()

/**
 * Subscribes to the moments where cached data must not outlive the session
 * that fetched it: a sign-out, a session the backend rejected, a session whose
 * own renewal could not satisfy it, and a fresh sign-in. The app layer registers
 * the query cache here rather than the store importing it, which would couple
 * the store to the query client's mount.
 */
export function onSessionChange(listener: SessionListener): () => void {
  sessionListeners.add(listener)
  return () => {
    sessionListeners.delete(listener)
  }
}

function announceSessionChange(): void {
  for (const listener of [...sessionListeners]) listener()
}

/**
 * Ends the session: the pair and the account go, and the announcement is what
 * drops the query cache. Module-level so the API client can end a session it
 * has decided is unrecoverable without the store growing an action that exists
 * for exactly one caller.
 */
function endSession(): void {
  renewedPair = null
  useAuthStore.setState({ ...ANONYMOUS, pending: false, error: null })
  announceSessionChange()
}

/**
 * How a refresh attempt ended.
 *
 * `unreachable` is kept apart from `unusable` because a backend that did not
 * answer says nothing about the token: treating it as a rejection would sign the
 * user out over a network blip. `superseded` is a third answer again — the
 * rotation itself worked, but the store no longer holds the session it was
 * spending, so committing it would be writing across a sign-out or a newer
 * sign-in that landed while the request was on the wire.
 */
type RefreshOutcome =
  | { kind: 'refreshed'; tokens: TokenPair }
  | { kind: 'unusable' }
  | { kind: 'unreachable'; error: ApiError }
  | { kind: 'superseded' }

/**
 * What one attempt to establish who the stored pair belongs to concluded.
 *
 * `unverified` is deliberately not a verdict: the backend was down, slow or
 * erroring, and none of that says the session is bad. It is the only outcome
 * that buys another attempt; `rejected` and `verified` end the boot check, and
 * `superseded` means a sign-out or a newer sign-in owns the store now and this
 * attempt has nothing left to decide.
 */
type VerifyOutcome = 'verified' | 'unverified' | 'rejected' | 'superseded'

/**
 * Boot verification is retried inside the store, because `hydrate` has exactly
 * one caller and it runs once per mount: a backend that 500s on the first
 * `/auth/me` would otherwise never be asked again, and the user would be
 * stranded on a verdict the server never gave. The budget is small on purpose —
 * it is meant to ride out a blip, not to keep a boot screen up.
 */
const VERIFY_MAX_ATTEMPTS = 3
const VERIFY_RETRY_DELAY_MS = 500

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

/**
 * Module-scoped so they outlive any single store read. Refresh tokens are
 * single-use server-side, so a second concurrent rotation would fail and take
 * the winner's freshly stored pair down with it.
 */
let hydrateInFlight: Promise<void> | null = null
let refreshInFlight: Promise<RefreshOutcome> | null = null

/**
 * The pair this store's own rotation last committed, or `null` once the session
 * is over.
 *
 * A renewal replaces the pair without changing the session, so a caller that
 * guards a write on the token it spent sees a token it did not mint and would
 * call the session superseded. That reading is wrong for the client-side 401
 * recovery, which rotates out from under the request that triggered it, so the
 * renewal is recorded here: it is what separates "renewed while this call was
 * on the wire" — the same session, adoptable — from "a newer sign-in owns the
 * store", whose tokens are the only other ones a rotation can have replaced.
 * Cleared on sign-out; a sign-in writes a pair the backend has never renewed,
 * so a stale entry cannot outlive the session it describes.
 */
let renewedPair: TokenPair | null = null

/**
 * Whether the refresh token the store holds is the one `claim` names, counting
 * a renewal of that same pair as still ours. Used by the sign-in flows, which
 * must tear down the pair they failed to exchange for a user without touching a
 * session that replaced it.
 */
function isClaimed(claim: string | null, held: string | null): boolean {
  if (held === claim) return true
  return renewedPair !== null && held === renewedPair.refresh_token
}

function isUnreachable(error: ApiError): boolean {
  return error.isTransportError || error.isTimeout || error.status >= 500
}

/**
 * A `localStorage` that cannot fail. zustand guards only the accessor that
 * hands the storage over, never the writes themselves, so a `setItem` that
 * throws — a full quota, Safari's private browsing — would turn every store
 * write into a rejected promise: `login()` would reject after committing
 * `pending`, leaving the form stuck on "Signing in…"; `logout()` would never
 * reach its redirect; and `hydrate()` would leave the app on its boot screen
 * for good. Losing persistence is a degradation rather than a failure, so each
 * operation swallows its own error and the store keeps working from memory.
 */
function tolerantStorage(): StateStorage {
  const attempt = <T,>(operation: () => T): T | null => {
    try {
      return operation()
    } catch {
      return null
    }
  }
  return {
    getItem: (name) => attempt(() => window.localStorage.getItem(name)),
    setItem: (name, value) => attempt(() => window.localStorage.setItem(name, value)),
    removeItem: (name) => attempt(() => window.localStorage.removeItem(name)),
  }
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => {
      async function rotateRefreshToken(): Promise<RefreshOutcome> {
        // The session this rotation spends. Every outcome is reported against
        // *this* pair, because the store may have moved on while the request was
        // on the wire: `logout()`, `deleteAccount()` or a newer sign-in all
        // replace the refresh token while this call is in flight.
        const startedFrom = get().refreshToken
        if (!startedFrom) return { kind: 'unusable' }
        try {
          const tokens = await refreshRequest(startedFrom)
          // Committing an unconditional write here is what made a late rotation
          // undo a sign-out — resurrecting an `anonymous` status that still
          // carried a bearer — and what let one account's tokens land on a
          // newer sign-in's store. If the token we set out to spend is no
          // longer the one we hold, the result belongs to a session that is
          // over: drop it and touch nothing.
          if (get().refreshToken !== startedFrom) return { kind: 'superseded' }
          set({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token })
          renewedPair = tokens
          return { kind: 'refreshed', tokens }
        } catch (cause) {
          const error = toApiError(cause)
          // The rejection is about the token *we* spent, not about whatever
          // session the store holds now, so a failure arriving after a sign-out
          // or a newer sign-in must not be allowed to end that one.
          if (get().refreshToken !== startedFrom) return { kind: 'superseded' }
          return isUnreachable(error) ? { kind: 'unreachable', error } : { kind: 'unusable' }
        }
      }

      /** Single-flight: concurrent callers share one rotation of one token. */
      function refreshSession(): Promise<RefreshOutcome> {
        if (refreshInFlight) return refreshInFlight
        const attempt = rotateRefreshToken().finally(() => {
          refreshInFlight = null
        })
        refreshInFlight = attempt
        return attempt
      }

      /**
       * The session is unverified rather than rejected — the backend was down,
       * slow or erroring, which says nothing about the token. The pair stays
       * where it is and `status` stays `initializing`, so the guards render the
       * boot screen instead of bouncing a possibly-signed-in user to /login
       * with a live bearer still attached to their requests. The boot check
       * retries within its budget; only an exhausted budget may end the session.
       */
      function markUnverified(): void {
        set({ status: 'initializing', pending: false })
      }

      /**
       * Fetches the account the stored pair belongs to and marks the store
       * authenticated.
       *
       * `expectedAccessToken` is the pair the caller had just written, and it is
       * the guard on the write below: a sign-out or a newer sign-in that lands
       * while `GET /auth/me` is on the wire changes the token the store holds,
       * and committing then would resurrect a session that was deliberately
       * ended, or put this account's identity on a newer one's shell. The
       * caller is told which happened so it can decide without guessing.
       *
       * @param expectedAccessToken The access token this call wrote, so a
       *   superseded write is detected and skipped. Omitted only where the
       *   caller is restoring a pair it did not mint.
       * @param recoverOn401 Whether the API client may renew the session on a
       *   401 by itself. Off wherever the store is already running the renewal:
       *   a rotation the client commits mid-call replaces the very token the
       *   guard above was handed, and the check is then told the session was
       *   superseded when it was only renewed — at boot that ends the check with
       *   no verdict at all and the guards render the boot screen for good.
       * @returns true when the store was updated, false when the pair was
       *   superseded while the request was in flight.
       */
      async function loadUser(
        expectedAccessToken?: string,
        { recoverOn401 = true }: { recoverOn401?: boolean } = {},
      ): Promise<boolean> {
        const user = await fetchCurrentUser({ recoverOn401 })
        if (expectedAccessToken !== undefined && get().accessToken !== expectedAccessToken) {
          if (renewedPair === null || get().accessToken !== renewedPair.access_token) {
            return false
          }
        }
        set({ user, status: 'authenticated', pending: false, error: null })
        return true
      }

      async function recoverForRequest(): Promise<TokenRecovery> {
        const outcome = await refreshSession()
        if (outcome.kind === 'refreshed') {
          return { kind: 'renewed', token: outcome.tokens.access_token }
        }
        if (outcome.kind === 'unreachable') {
          // No verdict on the token: the pair stays for the next attempt, and
          // the client is told why so it can report the transport failure it
          // actually saw rather than the 401 that triggered the recovery.
          return { kind: 'unreachable', error: outcome.error }
        }
        if (outcome.kind === 'unusable') {
          endSession()
          return { kind: 'rejected' }
        }
        // `superseded` reports "do not replay this request" exactly as a
        // rejection does, but leaves the store alone: clearing here would sign
        // out the *newer* session over a rotation it never asked for.
        return { kind: 'superseded' }
      }

      async function renewAndVerify(): Promise<VerifyOutcome> {
        const outcome = await refreshSession()
        if (outcome.kind === 'superseded') return 'superseded'
        if (outcome.kind === 'unreachable') {
          markUnverified()
          return 'unverified'
        }
        if (outcome.kind === 'unusable') {
          endSession()
          return 'rejected'
        }
        const accessToken = outcome.tokens.access_token
        try {
          return (await loadUser(accessToken, { recoverOn401: false })) ? 'verified' : 'superseded'
        } catch (cause) {
          const error = toApiError(cause)
          // A verdict that arrives after a sign-out or a newer sign-in is not
          // about the session the store holds now.
          if (get().accessToken !== accessToken) return 'superseded'
          if (isUnreachable(error)) {
            markUnverified()
            return 'unverified'
          }
          endSession()
          return 'rejected'
        }
      }

      /** One attempt: the stored pair as it stands, rotating only on a 401. */
      async function verifyOnce(accessToken: string | null): Promise<VerifyOutcome> {
        try {
          const verified = await loadUser(accessToken ?? undefined, { recoverOn401: false })
          return verified ? 'verified' : 'superseded'
        } catch (cause) {
          const error = toApiError(cause)
          // A 401 means the access token is spent; anything else — including a
          // timeout, a 5xx, or a 404 from a wrong base URL — says nothing about
          // the session's validity and is not a verdict at all.
          if (!error.isUnauthorized) return 'unverified'
          return renewAndVerify()
        }
      }

      async function verifyPersistedSession(): Promise<void> {
        for (let attempt = 1; attempt <= VERIFY_MAX_ATTEMPTS; attempt += 1) {
          const { accessToken, refreshToken } = get()
          if (!accessToken && !refreshToken) {
            endSession()
            return
          }
          const outcome = await verifyOnce(accessToken)
          if (outcome === 'verified' || outcome === 'rejected') return
          // A sign-out or a newer sign-in owns the store now; this check has
          // nothing left to decide about it and must not decide anything.
          if (outcome === 'superseded') return
          if (attempt < VERIFY_MAX_ATTEMPTS) {
            markUnverified()
            await delay(VERIFY_RETRY_DELAY_MS)
          }
        }
        // The budget is spent and the backend never answered. The pair cannot be
        // shown to be good, and leaving it in place would keep attaching a
        // bearer to every request from a shell that has already given up on
        // it — so this, and only this, is where a non-401 ends the session.
        endSession()
      }

      return {
        ...ANONYMOUS,
        status: 'initializing',
        pending: false,
        error: null,

        async login(payload) {
          set({ pending: true, error: null })
          // The refresh token this call is responsible for: the one it finds at
          // the start, then the one it mints. Teardown below runs only while that
          // is still what the store holds, so a sign-in that lands while this one
          // is on the wire is never signed out by this one failing.
          let owner = get().refreshToken
          try {
            const tokens = await loginRequest(payload)
            set({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token })
            owner = tokens.refresh_token
            // `loadUser` reports whether the pair we just wrote is still ours
            // when its own request comes back; if it is not, a newer sign-in
            // owns the store and this one has nothing left to announce.
            if (!(await loadUser(tokens.access_token))) return
            announceSessionChange()
          } catch (cause) {
            // A pair we never exchanged for a user must not survive: it is
            // persisted, and the next verification would adopt it as a session.
            // A sign-out or a newer sign-in that landed while this call was on
            // the wire owns the store now, and its pair is not this one's to
            // tear down.
            if (isClaimed(owner, get().refreshToken)) {
              endSession()
            }
            // Reported either way: the form that triggered this needs to know
            // the sign-in failed, whichever session the store ended up on.
            set({ error: toApiError(cause) })
          } finally {
            // `pending` belongs to that form, and every way out of this call —
            // a superseded pair, a failed load, a failure under a session that
            // is no longer this one's — has to release it. Without this the
            // inputs and the button stay disabled until the page is reloaded.
            set({ pending: false })
          }
        },

        async register(payload) {
          set({ pending: true, error: null })
          let owner = get().refreshToken
          try {
            await registerRequest(payload)
            const tokens = await loginRequest({
              email: payload.email,
              password: payload.password,
            })
            set({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token })
            owner = tokens.refresh_token
            if (!(await loadUser(tokens.access_token))) return
            announceSessionChange()
          } catch (cause) {
            if (isClaimed(owner, get().refreshToken)) {
              endSession()
            }
            set({ error: toApiError(cause) })
          } finally {
            // Same invariant as `login`: the create-account form is released on
            // every exit, not only the one that reported an error.
            set({ pending: false })
          }
        },

        async logout() {
          set({ pending: true })
          const { accessToken, refreshToken } = get()
          try {
            await logoutRequest(accessToken, refreshToken)
          } catch {
            // A failed revoke leaves a record on the server, but the local
            // session has to go either way.
          } finally {
            endSession()
          }
        },

        async logoutAll() {
          set({ pending: true, error: null })
          try {
            await logoutAllRequest()
          } catch (cause) {
            // Every other device is still live; say so rather than pretending
            // the sign-out happened.
            set({ pending: false, error: toApiError(cause) })
            return
          }
          // The endpoint spares the caller's own session, but the local session
          // is ended all the same — endSession() announces the change, so the
          // query cache goes with it and the next sign-in starts on the one
          // device that is left.
          endSession()
        },

        async changePassword(payload) {
          set({ pending: true, error: null })
          try {
            await changePasswordRequest(payload)
          } catch (cause) {
            set({ pending: false, error: toApiError(cause) })
            return
          }
          // Not a session transition: this device's tokens stay valid, so no
          // announcement — the cache is still good data.
          set({ pending: false, error: null })
        },

        async updateProfile(payload) {
          set({ pending: true, error: null })
          try {
            const user = await updateProfileRequest(payload)
            // No session announcement: a profile edit is not a session
            // transition, and dropping the cache here would throw away data the
            // new `user` still authorises.
            set({ user, pending: false, error: null })
          } catch (cause) {
            set({ pending: false, error: toApiError(cause) })
          }
        },

        async deleteAccount(payload) {
          set({ pending: true, error: null })
          try {
            await deleteAccountRequest(payload)
          } catch (cause) {
            set({ pending: false, error: toApiError(cause) })
            return
          }
          // The account and its sessions are gone, so this operation inherently
          // ends the session. endSession() announces it, which is what drops
          // the React Query cache.
          endSession()
        },

        hydrate() {
          if (hydrateInFlight) return hydrateInFlight
          const attempt = verifyPersistedSession().finally(() => {
            hydrateInFlight = null
          })
          hydrateInFlight = attempt
          return attempt
        },

        recoverSession: recoverForRequest,

        clearError() {
          set({ error: null })
        },
      }
    },
    {
      name: AUTH_STORAGE_KEY,
      storage: createJSONStorage(tolerantStorage),
      // `status` is deliberately not persisted: it is recomputed by `hydrate`.
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
      }),
    },
  ),
)

/** Supplies the bearer token to the shared client for every authenticated call. */
apiClient.setTokenGetter(() => useAuthStore.getState().accessToken)

/**
 * Lets a 401 on an ordinary call renew the session once and replay the request
 * that hit it, so an expired access token no longer strands a signed-in user.
 * A renewal that never reached the backend comes back as `unreachable` and a
 * session that moved on mid-rotation as `superseded`, so the client can report
 * what it actually saw instead of a 401 the server never meant.
 */
apiClient.setUnauthorizedHandler(() => useAuthStore.getState().recoverSession())

/**
 * A 401 that survives a successful renewal, several requests running, means the
 * endpoint is refusing every bearer the store mints. Rotating again would spend
 * a single-use refresh token per request and never resolve, so the streak is
 * counted and the session is ended once.
 */
apiClient.setSessionRejectedHandler(() => endSession())

/**
 * The name to show for an account, best available first: the chosen display
 * name, then the handle the account signed up with, then the local part of the
 * email address. There is always an answer — a signed-in user must never be
 * rendered as "undefined" — so an account that has set none of the three still
 * gets something stable and recognisable.
 */
export function selectDisplayName(user: User | null): string {
  if (!user) return 'Guest'
  const display = user.display_name?.trim()
  if (display && display.length > 0) return display
  const username = user.username?.trim()
  if (username && username.length > 0) return username
  return user.email.split('@')[0] || user.email
}

export function selectInitials(user: User | null): string {
  const display = selectDisplayName(user)
  const parts = display.split(/[\s.@_-]+/).filter(Boolean)
  if (parts.length === 0) return '?'
  const initials = parts.slice(0, 2).map((part) => part.charAt(0).toUpperCase())
  return initials.join('')
}
