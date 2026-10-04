/**
 * The Command Center's data hooks.
 *
 * **Every panel owns its own query.** The page's contract is that one failing
 * request must not blank the page, and that can only be true if the panels do
 * not share a single `useQuery`. Each hook below is therefore independent: a
 * rejected `GET /planner/conflicts` leaves the risk panel, the analytics panel
 * and the ML panel exactly as they were.
 *
 * **The hooks reuse the existing feature hooks rather than re-querying.** The
 * tasks, risks, recommendations, analytics, planner and learning reads all go
 * through the query keys their own feature already owns, so a task created from
 * the quick actions invalidates the same cache the Tasks page reads from, and
 * the two surfaces cannot drift.
 *
 * `useMlStatus` is the exception: no other feature holds a query for
 * `GET /ml/status`, so the key lives here and is exported for reuse.
 */
import { useQuery } from '@tanstack/react-query'
import type { UseQueryResult } from '@tanstack/react-query'

import { fetchMlStatus } from '@/services/ml'
import type { MlStatusRead } from '@/types/ml'

export const commandCenterKeys = {
  mlStatus: () => ['command-center', 'ml', 'status'] as const,
}

/**
 * What the classifier runtime is doing right now.
 *
 * `GET /ml/status` answers 200 even when the classifier is disabled or the
 * checkpoint is missing, so `available: false` renders as data and only a
 * transport or server failure becomes a panel error. Retries are left to the
 * shared query client for the same reason `services/ml.ts` declines to set one:
 * a 503 here means the runtime is not loaded, and asking again a second later
 * produces the same 503 more slowly.
 */
export function useMlStatus(options: { enabled?: boolean } = {}): UseQueryResult<MlStatusRead> {
  return useQuery({
    queryKey: commandCenterKeys.mlStatus(),
    queryFn: ({ signal }) => fetchMlStatus(signal),
    enabled: options.enabled,
  })
}
