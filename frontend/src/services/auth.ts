import { apiClient } from '@/lib/api-client'
import type { RequestOptions } from '@/lib/api-client'
import type {
  LoginPayload,
  PasswordChangePayload,
  PasswordResetConfirmPayload,
  PasswordResetRequestPayload,
  PasswordResetRequestedResponse,
  RefreshPayload,
  RegisterPayload,
  TokenPair,
  User,
} from '@/types'

export const AUTH_ENDPOINTS = {
  login: '/auth/login',
  register: '/auth/register',
  refresh: '/auth/refresh',
  logout: '/auth/logout',
  logoutAll: '/auth/logout-all',
  me: '/auth/me',
  changePassword: '/auth/password',
  forgotPassword: '/auth/password/forgot',
  resetPassword: '/auth/password/reset',
} as const

export function loginRequest(payload: LoginPayload): Promise<TokenPair> {
  return apiClient.post<TokenPair>(AUTH_ENDPOINTS.login, payload, { auth: false })
}

export function registerRequest(payload: RegisterPayload): Promise<User> {
  return apiClient.post<User>(AUTH_ENDPOINTS.register, payload, { auth: false })
}

export function refreshRequest(refreshToken: string): Promise<TokenPair> {
  return apiClient.post<TokenPair>(
    AUTH_ENDPOINTS.refresh,
    { refresh_token: refreshToken } satisfies RefreshPayload,
    { auth: false },
  )
}

/**
 * Best-effort server-side session teardown. Both tokens are presented: the
 * refresh one in the body so the backend revokes it rather than leaving it
 * usable until it expires, the access one in the Authorization header so it is
 * denylisted too. The header is passed explicitly with `auth: false`, so an
 * access token that has already expired cannot trigger the client's 401
 * recovery and rotate the very refresh token this call is revoking. Local state
 * is cleared by the caller regardless of the outcome, so a failure here must
 * never trap the user in a signed-in shell.
 */
export function logoutRequest(accessToken: string | null, refreshToken: string | null): Promise<void> {
  return apiClient.post<void>(
    AUTH_ENDPOINTS.logout,
    refreshToken ? ({ refresh_token: refreshToken } satisfies RefreshPayload) : undefined,
    {
      parse: 'none',
      auth: false,
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    },
  )
}

/**
 * Read the signed-in user.
 *
 * `recoverOn401` is exposed because the boot check drives its own renewal: if
 * the client rotated the session underneath it, the store's supersession guard
 * would see a different access token and discard the result, leaving the app
 * on its boot screen forever. Passing `false` keeps the recovery in the store,
 * where it is single-flighted and knows how to re-verify afterwards.
 */
export function fetchCurrentUser(
  options: Omit<RequestOptions, 'method' | 'body'> = {},
): Promise<User> {
  return apiClient.get<User>(AUTH_ENDPOINTS.me, options)
}

/**
 * Sign out every *other* device and stay signed in here. The caller's own
 * session is spared by its `sid` claim, so this deliberately runs on the normal
 * authenticated path: a token is needed to identify which session to spare.
 */
export function logoutAllRequest(): Promise<void> {
  return apiClient.post<void>(AUTH_ENDPOINTS.logoutAll, undefined, { parse: 'none' })
}

/**
 * The backend revokes every session but the caller's as part of this call, so
 * the tokens already held stay valid and the caller is not bounced to the login
 * screen after a successful change.
 */
export function changePasswordRequest(payload: PasswordChangePayload): Promise<void> {
  return apiClient.patch<void>(AUTH_ENDPOINTS.changePassword, payload, { parse: 'none' })
}

/**
 * Unauthenticated by necessity — the caller has lost their password. The
 * response is identical for registered and unregistered addresses, so the caller
 * must not branch on its contents.
 */
export function requestPasswordResetRequest(
  payload: PasswordResetRequestPayload,
): Promise<PasswordResetRequestedResponse> {
  return apiClient.post<PasswordResetRequestedResponse>(
    AUTH_ENDPOINTS.forgotPassword,
    payload,
    { auth: false },
  )
}

/** Redeems the reset token, which is a credential in its own right. */
export function resetPasswordRequest(payload: PasswordResetConfirmPayload): Promise<void> {
  return apiClient.post<void>(AUTH_ENDPOINTS.resetPassword, payload, {
    parse: 'none',
    auth: false,
  })
}
