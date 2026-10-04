import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { LucideIcon } from 'lucide-react'
import { AlertTriangle, Bot, Globe, LogOut, Monitor, MonitorX, Power, Smartphone, Tablet } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { describeUserAgent, formatRelativeTime } from '@/features/auth/session-labels'
import { toApiError } from '@/services/errors'
import { fetchSessions, revokeSession } from '@/services/sessions'
import { useAuthStore } from '@/stores/auth-store'
import { toast } from '@/stores/toast-store'
import type { AuthSession, SessionListResponse } from '@/types'

const SESSIONS_QUERY_KEY = ['auth', 'sessions'] as const

/** `SessionLabel.iconHint` values; anything unexpected falls back to a monitor. */
const DEVICE_ICONS: Readonly<Record<string, LucideIcon>> = {
  bot: Bot,
  globe: Globe,
  monitor: Monitor,
  smartphone: Smartphone,
  tablet: Tablet,
}

function DeviceGlyph({ hint, className }: { hint: string; className?: string }) {
  const Icon = DEVICE_ICONS[hint] ?? Monitor
  return <Icon className={className} aria-hidden="true" />
}

function recency(session: AuthSession): number {
  const raw = session.last_used_at ?? session.created_at
  const parsed = new Date(raw).getTime()
  return Number.isNaN(parsed) ? 0 : parsed
}

/**
 * Whether a row is still a live sign-in.
 *
 * The backend is being changed to return only live rows; until that lands this
 * panel stays honest on its own rather than counting a device the person can
 * no longer use. A session is dead if it was revoked or if its token has
 * already expired, and an unreadable `expires_at` counts as expired — a
 * timestamp we cannot parse is not evidence of a working session.
 */
function isExpired(session: AuthSession, now: Date): boolean {
  if (session.revoked_at !== null) return true
  const expiry = new Date(session.expires_at).getTime()
  if (Number.isNaN(expiry)) return true
  return expiry <= now.getTime()
}

function SessionSkeleton() {
  return (
    <li className="flex items-center gap-4 py-4">
      <Skeleton className="size-9 shrink-0 rounded-md" />
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton className="h-3.5 w-40" />
        <Skeleton className="h-3 w-56" />
      </div>
      <Skeleton className="h-8 w-16 shrink-0" />
    </li>
  )
}

export function SessionsPanel() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const logout = useAuthStore((state) => state.logout)
  const logoutAll = useAuthStore((state) => state.logoutAll)

  const [pendingSession, setPendingSession] = useState<AuthSession | null>(null)
  const [confirmSignOutEverywhere, setConfirmSignOutEverywhere] = useState(false)

  const sessions = useQuery<SessionListResponse>({
    queryKey: SESSIONS_QUERY_KEY,
    queryFn: () => fetchSessions(),
  })

  const revoke = useMutation({
    mutationFn: async (session: AuthSession) => {
      try {
        await revokeSession(session.id)
      } catch (cause) {
        // A 404 is the documented answer for an id that is already gone (or
        // never was): the row is revoked either way, so it is not a failure.
        const error = toApiError(cause)
        if (!error.isNotFound) throw error
      }
    },
    onSuccess: (_data, session) => {
      setPendingSession(null)
      if (session.is_current) {
        // The token used to make this request is now revoked, so the local
        // session has to be torn down as well; the route guard takes it from
        // there. The list is not refetched — there is no session left to list.
        const toLogin = (): void => {
          navigate('/login', { replace: true })
        }
        // Either way the redirect happens: the local session is gone even when
        // its teardown threw, and leaving the user on this screen would show a
        // session list for a session that no longer exists.
        void logout().then(toLogin, toLogin)
        return
      }
      toast.success('Session revoked')
      // No optimistic removal: the row is only dropped once the backend has
      // confirmed the revoke, so a failed call leaves the list truthful.
      void queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY })
    },
    onError: (cause) => {
      setPendingSession(null)
      toast.error('Could not revoke that session', toApiError(cause).message)
    },
  })

  const signOutEverywhere = useMutation({
    mutationFn: async () => {
      await logoutAll()
      // The store reports a failed revoke through `error` rather than by
      // throwing, so a silent "success" has to be ruled out here — otherwise the
      // user is told they were signed out while their other devices are still in.
      const error = useAuthStore.getState().error
      if (error) throw error
      // `logout-all` spares the caller's own session on the server; "everywhere"
      // is supposed to include this browser too, so the local session is ended
      // unless the store already did it as part of the call.
      if (useAuthStore.getState().status === 'authenticated') await logout()
    },
    onSuccess: () => {
      setConfirmSignOutEverywhere(false)
      toast.success('Signed out everywhere', 'Sign in again to continue.')
      navigate('/login', { replace: true })
    },
    onError: (cause) => {
      toast.error('Could not sign out everywhere', toApiError(cause).message)
    },
  })

  // This device first, then the most recently used. The list is the answer to
  // "which of these is mine?", and the current session is the one row whose
  // answer changes what the others mean.
  const ordered = useMemo(() => {
    const list = sessions.data?.sessions ?? []
    return [...list].sort((a, b) => {
      if (a.is_current !== b.is_current) return a.is_current ? -1 : 1
      return recency(b) - recency(a)
    })
  }, [sessions.data])

  // One clock reading for the whole pass, so a session cannot be counted as
  // live in the summary and rendered as expired a few lines below it.
  const now = new Date()
  const isLive = (session: AuthSession) => !isExpired(session, now)

  // A dead row stays in the list — it is still a row somebody may want to clear
  // out — but it is not a device, so it leaves every count, including the
  // `otherCount` that decides whether "Sign out everywhere" has anything to do.
  const activeCount = ordered.filter(isLive).length
  const otherCount = ordered.filter((session) => isLive(session) && !session.is_current).length
  const canSignOutEverywhere = !sessions.isPending && !sessions.isError && otherCount > 0

  const countSummary = (() => {
    if (activeCount === 0) return 'No active devices — every session below has expired or been revoked.'
    if (activeCount > 1) {
      return `${activeCount} devices signed in · ${otherCount} other${otherCount === 1 ? '' : 's'}`
    }
    // Only the current row gets the reassuring line; a lone live session that
    // is not this one is the interesting case, not a trivial one.
    return otherCount === 0 ? '1 device signed in — this one.' : '1 other device signed in.'
  })()

  const pendingLabel = pendingSession ? describeUserAgent(pendingSession.user_agent) : null

  return (
    <Card>
      <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:space-y-0">
        <div className="min-w-0 space-y-1.5">
          <CardTitle>Active sessions</CardTitle>
          <CardDescription>
            Every device signed in to this account, including any that have since expired.
            Revoking one takes effect on its next request.
          </CardDescription>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canSignOutEverywhere || signOutEverywhere.isPending}
            onClick={() => setConfirmSignOutEverywhere(true)}
          >
            {signOutEverywhere.isPending ? <Spinner size="sm" /> : <Power aria-hidden="true" />}
            Sign out everywhere
          </Button>
        </div>
      </CardHeader>

      <CardContent>
        {sessions.isPending ? (
          <ul className="divide-y divide-border">
            <SessionSkeleton />
            <SessionSkeleton />
            <SessionSkeleton />
          </ul>
        ) : sessions.isError ? (
          <ErrorState
            error={toApiError(sessions.error)}
            onRetry={() => void sessions.refetch()}
          />
        ) : ordered.length === 0 ? (
          <EmptyState
            icon={MonitorX}
            title="No active sessions"
            description="NEXUS has nothing to revoke right now. Devices appear here as soon as they sign in to this account."
            compact
          />
        ) : (
          <>
            <p className="pb-3 text-xs text-muted-foreground">{countSummary}</p>
            <ul className="divide-y divide-border">
              {ordered.map((session) => {
                const label = describeUserAgent(session.user_agent)
                const expired = !isLive(session)
                return (
                  <li
                    key={session.id}
                    className="flex flex-col gap-3 py-4 first:pt-1 sm:flex-row sm:items-center sm:gap-4"
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground">
                      <DeviceGlyph hint={label.iconHint} className="size-4" />
                    </span>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <p className="truncate text-sm font-medium text-foreground">
                          {label.label}
                        </p>
                        {session.is_current ? (
                          <Badge variant="default">This device</Badge>
                        ) : null}
                        {expired ? <Badge variant="secondary">Expired</Badge> : null}
                      </div>
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">
                        {expired ? (
                          <>Expired {formatRelativeTime(session.expires_at)}</>
                        ) : (
                          <>Last active: {formatRelativeTime(session.last_used_at)}</>
                        )}
                        {session.ip_address ? (
                          <>
                            {' · '}
                            <span className="font-mono text-foreground/70">
                              {session.ip_address}
                            </span>
                          </>
                        ) : null}
                      </p>
                    </div>

                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="shrink-0 self-start text-muted-foreground hover:text-destructive sm:self-auto"
                      onClick={() => setPendingSession(session)}
                    >
                      Revoke
                    </Button>
                  </li>
                )
              })}
            </ul>
          </>
        )}
      </CardContent>

      <Dialog
        open={pendingSession !== null}
        onOpenChange={(open) => {
          if (!open) setPendingSession(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Revoke this session?</DialogTitle>
            <DialogDescription>
              {pendingSession?.is_current
                ? 'This is the device you are using right now. Revoking it signs you out here and you will need to sign in again.'
                : 'That device is signed out immediately. It will have to sign in again with your password.'}
            </DialogDescription>
          </DialogHeader>

          {pendingSession && (
            <div className="flex items-center gap-3 rounded-md border border-border bg-muted/40 p-3">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
                <DeviceGlyph hint={pendingLabel?.iconHint ?? 'monitor'} className="size-4" />
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">
                  {pendingLabel?.label}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  Last active: {formatRelativeTime(pendingSession.last_used_at)}
                </p>
              </div>
            </div>
          )}

          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              disabled={revoke.isPending || pendingSession === null}
              onClick={() => {
                if (pendingSession) revoke.mutate(pendingSession)
              }}
            >
              {revoke.isPending ? <Spinner size="sm" /> : <LogOut aria-hidden="true" />}
              {revoke.isPending ? 'Revoking…' : 'Revoke session'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={confirmSignOutEverywhere}
        onOpenChange={(open) => {
          if (!open && !signOutEverywhere.isPending) setConfirmSignOutEverywhere(false)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Sign out of every device?</DialogTitle>
            <DialogDescription>
              {otherCount} other {otherCount === 1 ? 'device' : 'devices'} will be signed out, and
              so will this one. You will need to sign in again here.
            </DialogDescription>
          </DialogHeader>

          <div className="flex items-start gap-3 rounded-md border border-warning/30 bg-warning/[0.06] p-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
            <p className="text-sm leading-relaxed text-muted-foreground">
              Use this if you think someone else has your password. Changing your password is
              the other way to do it.
            </p>
          </div>

          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              disabled={signOutEverywhere.isPending}
              onClick={() => signOutEverywhere.mutate()}
            >
              {signOutEverywhere.isPending ? (
                <Spinner size="sm" />
              ) : (
                <Power aria-hidden="true" />
              )}
              {signOutEverywhere.isPending ? 'Signing out…' : 'Sign out everywhere'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
