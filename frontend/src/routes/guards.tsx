import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { Brand } from '@/components/brand/logo'
import { Spinner } from '@/components/ui/spinner'
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

export interface RequireAuthProps {
  children: ReactNode
}

/** Gate for the application shell. Remembers where the user was headed. */
export function RequireAuth({ children }: RequireAuthProps) {
  const status = useAuthStore((state) => state.status)
  const location = useLocation()

  if (status === 'initializing') return <BootScreen />

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
  // Signing in lands on the Command Center, not the Dashboard: it is the route
  // `/` already redirects to, and the two disagreeing would send a signed-in user
  // to two different places depending on how they arrived.
  if (status === 'authenticated') return <Navigate to="/command-center" replace />

  return children
}