import { useEffect } from 'react'
import type { ReactNode } from 'react'

import { queryClient } from '@/app/query-client'
import { onSessionChange, useAuthStore } from '@/stores/auth-store'

/**
 * Cached queries belong to the session that fetched them, so a sign-out, a
 * rejected session or a fresh sign-in drops them rather than serving one
 * account's data to the next. Registered here because the store must not
 * depend on the query client it is mounted beside.
 */
onSessionChange(() => {
  queryClient.clear()
})

/**
 * Verifies a persisted session before the first protected route renders.
 * Mounted inside the providers so both the router and the store see the same
 * lifecycle; the store single-flights the call, so StrictMode's double-mount
 * verifies once.
 */
export function AuthBootstrap({ children }: { children: ReactNode }) {
  const hydrate = useAuthStore((state) => state.hydrate)

  useEffect(() => {
    // `hydrate` reports its verdict through the store rather than by throwing,
    // but nothing it can be faulted on — storage, a listener in
    // `announceSessionChange` — may leave an unhandled rejection behind: the
    // guards are already showing the boot screen, and no retry follows it.
    void hydrate().catch(() => undefined)
  }, [hydrate])

  return children
}
