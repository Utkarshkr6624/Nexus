/**
 * Bring a repository's recorded history up to date while the page is open.
 *
 * **Why this exists.** Every figure on the Developer surface is a snapshot: the
 * backend reads a folder with `git` and writes what it found, and the stored row
 * is what the card renders. So the honest sentence is "as of the last scan",
 * and a user who commits and comes back sees yesterday's numbers — which reads
 * as NEXUS having missed the commit rather than as a snapshot being old. A
 * button labelled "Scan now" puts that knowledge on the user to carry: they have
 * to know the number is stale, and go looking for the control.
 *
 * So the page refreshes what is refreshable on its own. The scan is incremental
 * — the backend passes `--since=<latest_commit_at>` — so a repository with
 * nothing new costs one `git log` that returns nothing, which is tens of
 * milliseconds, not a re-read of the history.
 *
 * **Why a poll, and not only an effect keyed on the rows.** Keying an effect on
 * the rows is necessary but not sufficient, and the reason is
 * `app/query-client.ts`: `refetchOnWindowFocus` is `false` and `staleTime` is
 * thirty seconds, so a query does not re-read itself while the reader sits and
 * looks at it. An effect that runs when the rows change would therefore fire on
 * mount, on a filter change and after an invalidation — and in the one case the
 * complaint is actually about, a commit made in a terminal while this tab stays
 * open, not at all. The reader would be left watching a number that is known to
 * be stale and watching it stay stale, which is the report this module exists to
 * answer.
 *
 * So the same effect runs on a timer as well, and the timer is bounded by three
 * limits rather than being a free-running refresh:
 *
 * - Only a stale snapshot is re-read. :data:`RESCAN_AFTER_MS` is the age below
 *   which the stored row is trusted as current.
 * - One scan per repository per :data:`RESCAN_THROTTLE_MS`, held in a
 *   module-level map so the limit survives unmounts — a remount mid-scan would
 *   otherwise reset it and fire a second scan behind the first.
 * - The poll only runs while the tab is in front of the reader, and it re-checks
 *   the moment the tab comes back. A hidden tab is not being read, so paying for
 *   git processes nobody is looking at is pure waste; a tab that has just come
 *   back is the exact moment the new commit exists and the reader is waiting.
 * - The scan runs in the background. The page renders from the cached row
 *   immediately; the figures update when the mutation settles. Nothing here
 *   blocks navigation or shows a spinner over content that already exists.
 */
import { useEffect } from 'react'

import { useScanRepository } from '@/features/developer/hooks'
import type { RepositoryRead } from '@/types/developer'

/**
 * How old a snapshot has to be before the page re-reads the folder.
 *
 * Long enough that navigating within a session does not re-scan, short enough
 * that a user who commits, switches to NEXUS and looks again sees the commit.
 * A scan of an unchanged repository is far cheaper than this interval, so the
 * bound is about not shelling out needlessly, not about protecting the machine.
 */
const RESCAN_AFTER_MS = 30_000

/**
 * The floor between two scans of the same repository, per process.
 *
 * Independent of {@link RESCAN_AFTER_MS} because that compares the snapshot's
 * own age, and a scan does not move the clock forward while one is still
 * running: without this, a page that mounts twice in quick succession would ask
 * twice for work already in flight.
 */
const RESCAN_THROTTLE_MS = 15_000

/**
 * How often the page re-checks whether a repository's snapshot has gone stale.
 *
 * The same number as {@link RESCAN_AFTER_MS}, on purpose. The poll only decides
 * *when the age is checked*, and checking more often than the age at which a
 * re-read would happen anyway would ask {@link shouldRescan} a question whose
 * answer is always "no". So one period is both the cost floor for a repository
 * with nothing new — one incremental `git log`, once per thirty seconds, not
 * once per render — and the shortest a commit made in another window can take to
 * appear here.
 */
const RESCAN_POLL_MS = RESCAN_AFTER_MS

/** When this process last asked for a scan, by repository id. */
const lastRequestedAt = new Map<string, number>()

/**
 * Ask for a scan of one repository, unless the row is already current or a
 * scan was asked for very recently.
 *
 * Exported for its own tests: the throttle is module state, so a test that
 * cannot reach in would otherwise be asserting against a shared clock.
 */
export function shouldRescan(
  repository: Pick<RepositoryRead, 'id' | 'last_scanned_at'>,
  now: number,
): boolean {
  const previous = lastRequestedAt.get(repository.id)
  if (previous !== undefined && now - previous < RESCAN_THROTTLE_MS) return false

  if (repository.last_scanned_at === null) return true
  const scannedAt = Date.parse(repository.last_scanned_at)
  // An unparsable timestamp is not a reason to skip: the row claims to be a
  // scan, the scan cannot be placed in time, and re-reading is the safe read.
  if (Number.isNaN(scannedAt)) return true
  return now - scannedAt >= RESCAN_AFTER_MS
}

/**
 * Refresh stale repositories: once when the rows arrive or change, then on every
 * poll tick while the page is in front of the reader.
 *
 * **The throttle entry is written before the request, inside the effect.** That
 * is what makes a StrictMode double-mount safe: React runs this effect, tears it
 * down and runs it again, and the second pass finds the repository already
 * claimed and skips it. Writing the bookkeeping afterwards — or, as this hook
 * used to, keeping the mutation in a ref updated *during render*, which
 * `react-hooks/refs` rightly refuses — puts a window between "decided" and
 * "recorded" in which the second pass can decide the same thing again.
 *
 * **Only `mutate` is taken from the mutation, not the result object.** TanStack
 * Query builds `mutate` as a `useCallback` over an observer created once per
 * mount, so it holds one identity for the life of the component and is safe as
 * an effect dependency. The result object beside it is a fresh object on every
 * state change, which is what forced the ref in the first place.
 */
export function useAutoRescan(repositories: readonly RepositoryRead[] | undefined): void {
  const { mutate } = useScanRepository()

  useEffect(() => {
    // Nothing registered, or nothing read yet: there is no row to judge, and an
    // interval over an empty list would keep a timer alive for no reason.
    if (!repositories || repositories.length === 0) return

    const refresh = () => {
      // A tab nobody is looking at does not need its figures current, and a
      // backgrounded browser will happily keep this firing.
      if (document.hidden) return
      const now = Date.now()
      for (const repository of repositories) {
        if (!shouldRescan(repository, now)) continue
        lastRequestedAt.set(repository.id, now)
        mutate({ id: repository.id })
      }
    }

    // Arrival, a re-read of the rows, and a StrictMode second pass all land
    // here; the throttle decides whether any of them is a real scan.
    refresh()

    // Coming back to the tab is the moment the reader is waiting on the numbers,
    // and nothing else would ask for them: the query does not refetch on focus.
    const onVisibilityChange = () => {
      if (!document.hidden) refresh()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    const timer = window.setInterval(refresh, RESCAN_POLL_MS)

    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [repositories, mutate])
}

/** Forget every throttle entry. Tests call this between cases. */
export function resetRescanThrottle(): void {
  lastRequestedAt.clear()
}