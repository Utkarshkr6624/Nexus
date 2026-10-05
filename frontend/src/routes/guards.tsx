import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { Brand } from '@/components/brand/logo'
import { ErrorState } from '@/components/feedback/error-state'
import { Spinner } from '@/components/ui/spinner'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'

/**
 * Shown while a persisted session is being verified. It is deliberately
 * branded and silent — the alternative is a flash of the login screen on every
 * reload for a signed-in user.
 *
 * Note for anyone tempted to import this from `auth-boundary.tsx`: that
 * boundary keeps its own private fallback rather than importing this, because
 * it is a Suspense placeholder for the lazy /login chunk, not a session check.
 * Collapsing the two would mean importing a session concept into a loading
 * boundary, so the duplication is deliberate and this is the only definition of
 * the boot screen.
 */
export function BootScreen() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background">
      <Brand />
      <Spinner label="Restoring session" />
    </div>
  )
}

/** Used only if the store reports `unreachable` without keeping the failure. */
const UNKNOWN_OUTAGE = new ApiError({
  status: 0,
  code: 'network_error',
  message: 'The backend did not answer the request that verifies this session.',
})

/**
 * Shown when a stored session exists but the backend never answered the check
 * on it.
 *
 * This is the whole point of not letting that read as "signed out": the login
 * form is a *claim* — the session is gone, sign in again — and an outage makes
 * that claim false. Redirecting to it destroyed a perfectly good token pair and
 * then told the user to type a password into a form they did not need, while
 * the backend they would be authenticating against was not answering anyway.
 * The Retry re-runs the same boot check, so recovering costs one click.
 */
export function SessionUnavailableScreen() {
  const bootError = useAuthStore((state) => state.bootError)
  const hydrate = useAuthStore((state) => state.hydrate)

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background px-4 py-12">
      <Brand />
      <div className="w-full max-w-md">
        <ErrorState
          error={bootError ?? UNKNOWN_OUTAGE}
          onRetry={() => {
            void hydrate()
          }}
        />
      </div>
      <p className="max-w-md text-center text-sm text-muted-foreground">
        You are still signed in. Nothing has been signed out — the app simply cannot reach the
        backend right now.
      </p>
    </div>
  )
}

export interface RequireAuthProps {
  children: ReactNode
}

/** Gate for the application shell. Remembers where the user was headed. */
export function RequireAuth({ children }: RequireAuthProps) {
  const status = useAuthStore((state) => state.status)
  const location = useLocation()

  if (status === 'initializing') return <BootScreen />
  // Neither an anonymous user nor an outage may be answered with the sign-in
  // form: one of them is not signed out, and the other cannot sign in either.
  if (status === 'unreachable') return <SessionUnavailableScreen />

  if (status !== 'authenticated') {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  return children
}

export interface RequireAnonymousProps {
  children: ReactNode
}

/** Keeps signed-in users out of the credential screens: /login, /register,
 * /forgot-password and /reset-password. */
export function RequireAnonymous({ children }: RequireAnonymousProps) {
  const status = useAuthStore((state) => state.status)

  if (status === 'initializing') return <BootScreen />
  // The same outage, on the same terms: showing the form would read as
  // "your session ended", and submitting it could not reach a backend.
  if (status === 'unreachable') return <SessionUnavailableScreen />
  // Signing in lands on the Command Center, not the Dashboard: it is the route
  // `/` already redirects to, and the two disagreeing would send a signed-in user
  // to two different places depending on how they arrived.
  if (status === 'authenticated') return <Navigate to="/command-center" replace />

  return children
}
