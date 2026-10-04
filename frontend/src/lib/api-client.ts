import type { ApiErrorBody, ApiErrorCode, ApiErrorEnvelope } from '@/types'

/**
 * Minimal typed fetch wrapper for the NEXUS API.
 *
 * Deliberately framework-agnostic: no React, no React Query. Callers (query
 * functions, stores, plain scripts) get JSON in, JSON out, and a single
 * `ApiError` type to branch on. React Query wraps this in a `queryFn`.
 */

/** Relative default. Requests then stay same-origin and hit the Vite dev proxy. */
export const DEFAULT_API_BASE_URL = '/api/v1'
export const DEFAULT_TIMEOUT_MS = 30_000

/** Sentinel status: the request never produced an HTTP response. */
const NO_HTTP_STATUS = 0

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

/** Sync or async supplier of the bearer token; wired to the auth store later. */
export type TokenGetter = () => string | null | undefined | Promise<string | null | undefined>

/**
 * What the recovery hook made of a 401.
 *
 * The client replays the failed request only for `renewed`. The other three all
 * mean "do not replay", but they are not the same answer and collapsing them
 * costs the caller the truth:
 *
 * - `rejected` — the backend refused the refresh token. The session is over and
 *   the original 401 is a fair report of that.
 * - `unreachable` — the renewal never reached the backend, so the 401 that
 *   triggered it is not evidence about the session. Throwing the transport
 *   error instead lets the caller retry on the failure it is actually looking
 *   at, rather than settle into a permanent 401 state it cannot retry out of.
 * - `superseded` — the store moved on while the renewal was in flight (a
 *   sign-out, or a newer sign-in). Its session must not be touched, and this
 *   request must not be replayed under whatever token lives in the store now.
 */
export type TokenRecovery =
  | { kind: 'renewed'; token: string }
  | { kind: 'rejected' }
  | { kind: 'unreachable'; error: ApiError }
  | { kind: 'superseded' }

/**
 * Recovery hook invoked once per 401 on a request the client authenticated. It
 * reports what it did with the session, as a `TokenRecovery`.
 */
export type UnauthorizedHandler = () => Promise<TokenRecovery>

/**
 * Called when the session has stopped answering to its own renewal. Fires once
 * per streak, not once per request: without it, a client whose 401 recovery can
 * never satisfy a given endpoint rotates a single-use refresh token on every
 * call and signs the user in forever.
 */
export type SessionRejectedHandler = () => void

/**
 * Requests in a row that came back 401 *after* a successful renewal. Two is
 * enough: one is the ordinary case of a token spent mid-flight, two means the
 * endpoint is refusing every bearer the store mints, and continuing past that
 * only burns refresh tokens.
 */
const MAX_CONSECUTIVE_RENEWAL_FAILURES = 2

export type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue>

/**
 * Turns a list endpoint's params object into a {@link QueryParams}.
 *
 * Three kinds of entry are dropped rather than sent: `undefined`, `null` and
 * the empty string would each serialise as a literal (`?q=` asks for the empty
 * query, which is a different request from not asking), and an array is not a
 * single query value at all — an endpoint that takes a list spells it the way
 * `docs/api-conventions.md` describes, not as a repeated key.
 *
 * **This is the app's only params filter.** Every service module used to carry
 * its own copy, and a copy is a place for the rule to drift: one that kept the
 * empty string would send `?search=` and get an empty result set back while
 * looking like a search that found nothing.
 *
 * Typed as `object` rather than `Record<string, unknown>` because an interface
 * carries no implicit index signature and would not be assignable to that
 * record — callers pass their own typed params interfaces directly.
 */
export function queryFrom(params: object): QueryParams {
  const query: QueryParams = {}
  for (const [key, value] of Object.entries(params) as Array<[string, unknown]>) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) continue
    query[key] = value as QueryValue
  }
  return query
}

export interface ApiErrorInit {
  status: number
  code: ApiErrorCode
  message: string
  details?: Record<string, unknown> | null
  requestId?: string | null
}

/** Every failure path of `ApiClient.request` throws this. */
export class ApiError extends Error {
  readonly status: number
  readonly code: ApiErrorCode
  readonly details: Record<string, unknown> | null
  readonly requestId: string | null

  constructor(init: ApiErrorInit) {
    super(init.message)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.details = init.details ?? null
    this.requestId = init.requestId ?? null
  }

  /** True when the request failed before any HTTP response arrived. */
  get isTransportError(): boolean {
    return this.status === NO_HTTP_STATUS
  }

  get isTimeout(): boolean {
    return this.code === 'timeout'
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  get isForbidden(): boolean {
    return this.status === 403
  }

  get isNotFound(): boolean {
    return this.status === 404
  }

  get isConflict(): boolean {
    return this.status === 409
  }

  get isValidationError(): boolean {
    return this.status === 422 || this.code === 'validation_error'
  }

  /** Field-level context from a `validation_error` envelope, if any. */
  get fieldErrors(): Record<string, unknown> {
    return this.details ?? {}
  }
}

export interface RequestOptions {
  method?: HttpMethod
  /** Serialised as JSON unless it is FormData/URLSearchParams/Blob/string. */
  body?: unknown
  query?: QueryParams
  headers?: HeadersInit
  /** Caller-side cancellation, e.g. React Query's per-query signal. */
  signal?: AbortSignal
  /** Overrides the client default. `0` disables the timeout. */
  timeoutMs?: number
  /**
   * Set false for endpoints that need no Authorization header, or that supply
   * their own. A request the client does not authenticate is also exempt from
   * the 401 recovery, which is what the login, refresh and logout calls need.
   */
  auth?: boolean
  /**
   * Set false for a request that must surface its own 401 rather than let the
   * client renew and replay it. The auth store's boot check is the one caller:
   * it performs the renewal itself, and a rotation the client commits mid-call
   * replaces the pair the store is guarding its write on.
   */
  recoverOn401?: boolean
  parse?: 'json' | 'text' | 'none'
}

export interface ApiClientOptions {
  baseUrl?: string
  timeoutMs?: number
  getToken?: TokenGetter | null
  fetchImpl?: typeof fetch
}

function resolveBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL?.trim()
  return configured ? configured : DEFAULT_API_BASE_URL
}

function joinUrl(base: string, path: string): string {
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path)) {
    return path
  }
  const trimmedBase = base.replace(/\/+$/, '')
  const trimmedPath = path.replace(/^\/+/, '')
  return trimmedPath ? `${trimmedBase}/${trimmedPath}` : trimmedBase
}

function buildSearchParams(query: QueryParams): URLSearchParams {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue
    params.append(key, String(value))
  }
  return params
}

function isNativeBody(body: unknown): body is BodyInit {
  return (
    typeof body === 'string' ||
    body instanceof FormData ||
    body instanceof URLSearchParams ||
    body instanceof Blob
  )
}

const STATUS_CODE_FALLBACK: Readonly<Record<number, ApiErrorCode>> = {
  400: 'validation_error',
  401: 'unauthorized',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  422: 'validation_error',
  429: 'rate_limited',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readErrorBody(payload: unknown): ApiErrorBody | null {
  if (!isRecord(payload)) return null
  // The envelope is `{ error: {...} }`; anything else is not ours.
  const { error } = payload as Partial<ApiErrorEnvelope>
  if (!isRecord(error) || typeof error.message !== 'string') return null
  return {
    code: typeof error.code === 'string' ? error.code : 'internal_error',
    message: error.message,
    details: isRecord(error.details) ? error.details : null,
    request_id: typeof error.request_id === 'string' ? error.request_id : '',
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  const headerRequestId = response.headers.get('x-request-id')
  let body: ApiErrorBody | null = null
  let fallbackText = ''

  try {
    const text = await response.text()
    if (text) {
      try {
        body = readErrorBody(JSON.parse(text) as unknown)
      } catch {
        fallbackText = text
      }
    }
  } catch {
    // Body already consumed or unreadable: the status-derived fallback still gives
    // the caller something actionable.
  }

  if (body) {
    return new ApiError({
      status: response.status,
      code: body.code,
      message: body.message,
      details: body.details,
      requestId: body.request_id || headerRequestId,
    })
  }

  const message = fallbackText.trim() || response.statusText || 'Request failed'
  return new ApiError({
    status: response.status,
    code: STATUS_CODE_FALLBACK[response.status] ?? 'internal_error',
    message: message.slice(0, 500),
    requestId: headerRequestId,
  })
}

/** One wording for every way a request can run out of time. */
function timeoutError(path: string, timeoutMs: number): ApiError {
  return new ApiError({
    status: NO_HTTP_STATUS,
    code: 'timeout',
    message: `Request to ${path} timed out after ${timeoutMs}ms`,
  })
}

export class ApiClient {
  private baseUrl: string
  private timeoutMs: number
  private getToken: TokenGetter | null
  private unauthorizedHandler: UnauthorizedHandler | null
  private sessionRejectedHandler: SessionRejectedHandler | null
  private fetchImpl: typeof fetch
  /** Shared 401 recovery, so a burst of parallel 401s renews the session once. */
  private recovery: Promise<TokenRecovery> | null = null
  /** Running count of requests that stayed 401 after a successful renewal. */
  private consecutiveRenewalFailures = 0
  /** Latches so the streak reports the session once, however long it runs. */
  private sessionRejectionReported = false

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? resolveBaseUrl()
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
    this.getToken = options.getToken ?? null
    this.unauthorizedHandler = null
    this.sessionRejectedHandler = null
    this.fetchImpl = options.fetchImpl ?? ((...args) => globalThis.fetch(...args))
  }

  /** Runtime reconfiguration, e.g. after auth is restored from storage. */
  configure(options: ApiClientOptions): void {
    if (options.baseUrl !== undefined) this.baseUrl = options.baseUrl
    if (options.timeoutMs !== undefined) this.timeoutMs = options.timeoutMs
    if (options.getToken !== undefined) this.getToken = options.getToken
    if (options.fetchImpl !== undefined) this.fetchImpl = options.fetchImpl
  }

  /** Installs (or clears) the bearer-token supplier. */
  setTokenGetter(getToken: TokenGetter | null): void {
    this.getToken = getToken
  }

  /** Installs (or clears) the hook that recovers an expired session on a 401. */
  setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
    this.unauthorizedHandler = handler
  }

  /** Installs (or clears) the hook that ends a session renewal cannot save. */
  setSessionRejectedHandler(handler: SessionRejectedHandler | null): void {
    this.sessionRejectedHandler = handler
  }

  getBaseUrl(): string {
    return this.baseUrl
  }

  buildUrl(path: string, query?: QueryParams): string {
    const url = joinUrl(this.baseUrl, path)
    if (!query) return url
    const search = buildSearchParams(query).toString()
    if (!search) return url
    return `${url}${url.includes('?') ? '&' : '?'}${search}`
  }

  /**
   * One round trip, with the caller's cancellation applied.
   * A transport failure leaves as an `ApiError`; anything HTTP-shaped is handed
   * back untouched so the caller can decide what a status means.
   *
   * The abort plumbing belongs to `request`, which owns the timeout: `fetch`
   * resolves on headers, so a timer scoped to this call would be cleared before
   * the body is read and a stalled body would never be noticed.
   */
  private async send(params: {
    path: string
    query: QueryParams | undefined
    method: HttpMethod
    headers: Headers
    payload: BodyInit | undefined
    signal: AbortSignal
    callerSignal: AbortSignal | undefined
    timeoutMs: number
    timedOut: () => boolean
  }): Promise<Response> {
    const { path, query, method, headers, payload, signal, callerSignal, timeoutMs, timedOut } = params

    try {
      return await this.fetchImpl(this.buildUrl(path, query), {
        method,
        headers,
        body: payload,
        signal,
        credentials: 'same-origin',
      })
    } catch (cause) {
      if (timedOut()) {
        throw timeoutError(path, timeoutMs)
      }
      if (callerSignal?.aborted) {
        // Caller cancelled: surface the abort reason so React Query can ignore it.
        throw callerSignal.reason ?? cause
      }
      throw new ApiError({
        status: NO_HTTP_STATUS,
        code: 'network_error',
        message: cause instanceof Error ? cause.message : 'Network request failed',
      })
    }
  }

  /**
   * Runs the recovery hook, collapsing concurrent 401s onto a single renewal. A
   * hook that throws is treated as "not recoverable": the original 401 is
   * surfaced to the caller instead of being retried.
   */
  private async recoverToken(): Promise<TokenRecovery> {
    const handler = this.unauthorizedHandler
    if (!handler) return { kind: 'rejected' }
    if (this.recovery) return this.recovery

    const recovery = (async () => {
      try {
        return await handler()
      } catch {
        return { kind: 'rejected' } as const
      }
    })().finally(() => {
      this.recovery = null
    })
    this.recovery = recovery
    return recovery
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const {
      method = 'GET',
      body,
      query,
      signal,
      auth = true,
      recoverOn401 = true,
      parse = 'json',
    } = options
    const timeoutMs = options.timeoutMs ?? this.timeoutMs

    if (signal?.aborted) {
      throw signal.reason ?? new DOMException('Request aborted', 'AbortError')
    }

    // The timeout spans the whole exchange, body included: `fetch` resolves on
    // headers, so a server that flushes them and then stalls the body would leave
    // this promise pending forever — and a promise that never settles is a query
    // that never retries.
    const controller = new AbortController()
    let timedOut = false
    const onExternalAbort = (): void => controller.abort()
    signal?.addEventListener('abort', onExternalAbort)

    const timer =
      timeoutMs > 0
        ? setTimeout(() => {
            timedOut = true
            controller.abort()
          }, timeoutMs)
        : undefined

    try {
      const headers = new Headers(options.headers)
      let payload: BodyInit | undefined

      if (body !== undefined && body !== null) {
        if (isNativeBody(body)) {
          payload = body
        } else {
          payload = JSON.stringify(body)
          if (!headers.has('Content-Type')) {
            headers.set('Content-Type', 'application/json')
          }
        }
      }

      // A caller-supplied Authorization header is the caller's own credential:
      // the client neither replaces it nor renews it on a 401.
      const getToken = auth ? this.getToken : null
      let authenticated = false
      if (getToken && !headers.has('Authorization')) {
        const token = await getToken()
        if (token) {
          headers.set('Authorization', `Bearer ${token}`)
          authenticated = true
        }
      }
      if (!headers.has('Accept')) {
        headers.set('Accept', 'application/json')
      }

      const send = (): Promise<Response> =>
        this.send({
          path,
          query,
          method,
          headers,
          payload,
          signal: controller.signal,
          callerSignal: signal,
          timeoutMs,
          timedOut: () => timedOut,
        })

      let response = await send()
      let rejectedAfterRenewal = false

      if (response.status === 401 && authenticated && recoverOn401) {
        const recovery = await this.recoverToken()
        if (recovery.kind === 'unreachable') {
          // The renewal never reached the backend, so this 401 is not evidence
          // that the session is spent. Reporting it would tell the caller the
          // backend rejected the session when in fact it never answered, and
          // React Query does not retry a 4xx — the query would sit in a wrong
          // error state for the rest of the cache's life.
          throw recovery.error
        }
        if (recovery.kind === 'renewed') {
          // Replay at most once: a second 401 is a real rejection, not an
          // expiry, and the caller sees it as such.
          headers.set('Authorization', `Bearer ${recovery.token}`)
          response = await send()
          rejectedAfterRenewal = response.status === 401
        }
      }

      // Only a request the client authenticated belongs to the streak. The
      // recovery call travels through this same client unauthenticated — login,
      // refresh and logout all pass `auth: false` — so counting it would reset
      // the counter it exists to advance, and the streak could never reach two.
      if (authenticated) {
        // A request that was not refused after a renewal breaks the streak, so
        // the count measures consecutive failures rather than total requests.
        if (!rejectedAfterRenewal) {
          this.consecutiveRenewalFailures = 0
          this.sessionRejectionReported = false
        } else {
          this.consecutiveRenewalFailures += 1
          if (
            this.consecutiveRenewalFailures >= MAX_CONSECUTIVE_RENEWAL_FAILURES &&
            !this.sessionRejectionReported
          ) {
            // Renewal succeeds and the endpoint still says 401, repeatedly: the
            // session cannot be saved by rotating again. Report it once, so the
            // loop stops instead of spending a single-use refresh token per
            // request for as long as the panel is open.
            this.sessionRejectionReported = true
            this.sessionRejectedHandler?.()
          }
        }
      }

      if (!response.ok) {
        const error = await toApiError(response)
        // A body that never arrived because the timeout fired is a timeout, not
        // the status the headers carried.
        throw timedOut ? timeoutError(path, timeoutMs) : error
      }

      if (parse === 'none' || response.status === 204) {
        return undefined as T
      }

      let text: string
      try {
        text = await response.text()
      } catch (cause) {
        if (timedOut) throw timeoutError(path, timeoutMs)
        throw cause
      }

      if (!text) {
        return undefined as T
      }
      if (parse === 'text') {
        return text as T
      }

      try {
        return JSON.parse(text) as T
      } catch {
        throw new ApiError({
          status: response.status,
          code: 'invalid_response',
          message: 'Response body was not valid JSON',
        })
      }
    } finally {
      if (timer !== undefined) clearTimeout(timer)
      signal?.removeEventListener('abort', onExternalAbort)
    }
  }

  get<T>(path: string, options: Omit<RequestOptions, 'method' | 'body'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'GET' })
  }

  post<T>(
    path: string,
    body?: unknown,
    options: Omit<RequestOptions, 'method'> = {},
  ): Promise<T> {
    return this.request<T>(path, { ...options, method: 'POST', body })
  }

  put<T>(path: string, body?: unknown, options: Omit<RequestOptions, 'method'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PUT', body })
  }

  patch<T>(
    path: string,
    body?: unknown,
    options: Omit<RequestOptions, 'method'> = {},
  ): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PATCH', body })
  }

  delete<T>(path: string, options: Omit<RequestOptions, 'method' | 'body'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'DELETE' })
  }
}

/** Shared client used by the app. Token wiring is attached by the auth store. */
export const apiClient = new ApiClient()
