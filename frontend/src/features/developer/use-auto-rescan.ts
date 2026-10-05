/**
 * Bring a repository's recorded history up to date when the page is opened.
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
 * **Why it is still bounded.** Three limits, because "refresh on open" would
 * otherwise shell out to git on every navigation for every repository:
 *
 * - Only a stale snapshot is refreshed. :data:`RESCAN_AFTER_MS` is the age below
 *   which the stored row is trusted as current.
 * - One scan per repository per :data:`RESCAN_THROTTLE_MS`, held in a
 *   module-level map so the limit survives unmounts — a remount mid-scan would
 *   otherwise reset it and fire a second scan behind the first.
 * - The scan runs in the background. The page renders from the cached row
 *   immediately; the figures update when the mutation settles. Nothing here
 *   blocks navigation or shows a spinner over content that already exists.
 */
import { useEffect, useRef } from 'react'

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
 * Refresh stale repositories whenever `repositories` changes.
 *
 * The effect is keyed on the rows themselves rather than on a timer: a timer
 * would keep running while the user sits on the page, re-reading folders for
 * commits they have not made yet.
 */
export function useAutoRescan(repositories: readonly RepositoryRead[] | undefined): void {
  const scan = useScanRepository()
  const scanRef = useRef(scan)
  scanRef.current = scan

  useEffect(() => {
    if (!repositories) return
    const now = Date.now()
    for (const repository of repositories) {
      if (!shouldRescan(repository, now)) continue
      lastRequestedAt.set(repository.id, now)
      scanRef.current.mutate({ id: repository.id })
    }
    // `scanRef` is a ref, so it is deliberately not a dependency: re-running on
    // a mutation-state change would re-enter this loop for the same rows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repositories])
}

/** Forget every throttle entry. Tests call this between cases. */
export function resetRescanThrottle(): void {
  lastRequestedAt.clear()
}