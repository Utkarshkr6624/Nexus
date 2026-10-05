/**
 * TanStack Query bindings for the Phase 8 developer intelligence surface.
 *
 * **`developerKeys` is the single owner of the query-key shape.** Every key
 * lives under the `['developer']` root and every *filtered or windowed* key
 * carries its resolved parameters, normalised to fixed length with `?? null` for
 * each unset field. That is not a style preference: a params object rebuilt on
 * every render hashes to the same key when unset fields become `null` and
 * thrashes the cache when they are dropped, so `useDeveloperActivity(params)`
 * with a fresh `{ window_days: 30 }` literal does not refetch on every keystroke
 * in the surrounding component.
 *
 * **Every mutation invalidates the whole `['developer']` tree.** A scan writes
 * commits, branches, a scan run and the repository's own `last_scanned_at`, and
 * every metric, the summary, the activity series and the feature vector is
 * derived from those rows. Picking the keys a scan "should" affect is how a
 * dashboard ends up showing a freshly scanned repository beside a metric that
 * still counts the scan before it. The tree is small — one account's own
 * repositories — so the aggregate is the correct trade.
 *
 * **A failed scan is not an error here.** `POST .../scan` is a 200 carrying
 * `ScanRunRead` with `status: 'error'` and a human sentence, so the mutation
 * resolves and the invalidation runs like any other; the page renders the
 * sentence. That is the mechanism behind "a broken repository must never break
 * NEXUS" — there is no error path for a bad repository to travel down.
 *
 * **The window lives in the URL**, via {@link useDeveloperWindow}, for the same
 * reason analytics keeps it there: `?range=90d` is a shareable view, and the
 * back button walks windows rather than leaving the page.
 *
 * **Retry policy is inherited.** `app/query-client.ts` refuses to retry a 4xx,
 * so the 422 from a rejected repository path and the 404 for another account's
 * repository both surface on the first response and are the page's to explain.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  createRepository,
  deleteRepository,
  fetchDeveloperActivity,
  fetchDeveloperCommits,
  fetchDeveloperMetrics,
  fetchDeveloperSummary,
  fetchRepositories,
  fetchRepository,
  fetchRepositoryBranches,
  fetchRepositoryCommits,
  scanRepository,
} from '@/services/developer'
import { toApiError } from '@/services/errors'
import type { DateOnlyString } from '@/types/analytics'
import { isDateOnly, rangeDays, shiftDays, todayDateOnly } from '@/types/analytics'
import type { PaginationParams } from '@/types/pagination'
import {
  ACTIVITY_GRANULARITIES,
  DEVELOPER_DEFAULT_ACTIVITY_GRANULARITY,
  DEVELOPER_DEFAULT_WINDOW_DAYS,
  MAX_DEVELOPER_WINDOW_DAYS,
  type ActivityGranularity,
  type ActivityParams,
  type BranchListRead,
  type CommitListParams,
  type CommitListRead,
  type DeveloperActivityRead,
  type DeveloperMetricRead,
  type DeveloperSummaryRead,
  type DeveloperWindowParams,
  type RepositoryCreatePayload,
  type RepositoryListParams,
  type RepositoryListRead,
  type RepositoryRead,
  type RepositoryScanParams,
  type ScanRunRead,
  type UUIDString,
} from '@/types/developer'

type Enabled = { enabled?: boolean }

/* -------------------------------------------------------------- key helpers */

/**
 * Window-only key part. `window_days` is optional because the backend owns the
 * default, so an unset window is a real, distinct request — recorded as `null`,
 * not omitted, because dropping it would collide with an explicit `30`.
 */
function windowKeyPart(params: DeveloperWindowParams = {}): unknown[] {
  return [params.window_days ?? null]
}

/** The activity series is a function of its window, its grain and its scope. */
function activityKeyPart(params: ActivityParams = {}): unknown[] {
  return [params.window_days ?? null, params.granularity ?? null, params.repository_id ?? null]
}

/**
 * Commit timelines: a page of a filtered set.
 *
 * `branch` is best-effort attribution server-side, so it is part of the key
 * because a filtered timeline and an unfiltered one are genuinely different
 * answers rather than the same rows in a different order.
 */
function commitKeyPart(params: CommitListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.repository_id ?? null,
    params.branch ?? null,
  ]
}

/** The repository list: a page of a filtered set. */
function repositoryKeyPart(params: RepositoryListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.is_active ?? null,
    params.project_id ?? null,
  ]
}

/** Branches are a short, unpaginated-in-practice list; only the page is keyed. */
function paginationKeyPart(params: PaginationParams = {}): unknown[] {
  return [params.limit ?? null, params.offset ?? null]
}

/** Stable key factory. Every key lives under the `['developer']` root. */
export const developerKeys = {
  all: () => ['developer'] as const,
  summary: (params: DeveloperWindowParams = {}) =>
    ['developer', 'summary', ...windowKeyPart(params)] as const,
  metrics: (params: DeveloperWindowParams = {}) =>
    ['developer', 'metrics', ...windowKeyPart(params)] as const,
  activity: (params: ActivityParams = {}) =>
    ['developer', 'activity', ...activityKeyPart(params)] as const,
  commits: (params: CommitListParams = {}) =>
    ['developer', 'commits', ...commitKeyPart(params)] as const,
  features: (params: DeveloperWindowParams = {}) =>
    ['developer', 'features', ...windowKeyPart(params)] as const,
  repositories: (params: RepositoryListParams = {}) =>
    ['developer', 'repositories', ...repositoryKeyPart(params)] as const,
  repository: (id: UUIDString) => ['developer', 'repository', id] as const,
  repositoryCommits: (id: UUIDString, params: CommitListParams = {}) =>
    ['developer', 'repository', id, 'commits', ...commitKeyPart(params)] as const,
  repositoryBranches: (id: UUIDString, params: PaginationParams = {}) =>
    ['developer', 'repository', id, 'branches', ...paginationKeyPart(params)] as const,
  project: (projectId: UUIDString, params: DeveloperWindowParams = {}) =>
    ['developer', 'project', projectId, ...windowKeyPart(params)] as const,
}

/* ------------------------------------------------------- window (in the URL) */

/**
 * The window shortcuts the developer picker offers.
 *
 * A developer window is a *trailing span*, not a pair of dates: the endpoints
 * accept `window_days` and resolve it backwards from today server-side. There is
 * therefore no separate `today` entry — analytics' one-day window is the `1d`
 * case here — and the widest preset matches {@link MAX_DEVELOPER_WINDOW_DAYS}
 * because anything wider is a 422.
 *
 * These are developer-specific on purpose rather than reusing the analytics
 * presets: `30d` here means "the server's own `developer_default_window_days`",
 * and pinning it client-side would make a link mean something different from
 * the account whose configuration changed.
 */
export type DeveloperWindowPresetId = '7d' | '30d' | '90d' | '180d' | '365d' | 'custom'

export interface DeveloperWindowPreset {
  id: DeveloperWindowPresetId
  label: string
  /** Undefined for `custom`, whose length comes from `?start`/`?end`. */
  days?: number
}

export const DEVELOPER_WINDOW_PRESETS: readonly DeveloperWindowPreset[] = [
  { id: '7d', label: '7 days', days: 7 },
  { id: '30d', label: '30 days', days: 30 },
  { id: '90d', label: '90 days', days: 90 },
  { id: '180d', label: '180 days', days: 180 },
  { id: '365d', label: '365 days', days: 365 },
  { id: 'custom', label: 'Custom' },
]

/**
 * The default preset, mirroring `developer_default_window_days`.
 *
 * Choosing the default means sending **no** `window_days` at all, so the backend
 * applies its own configured value. A client that pinned 30 would quietly ignore
 * an account whose default had been changed.
 */
export const DEFAULT_DEVELOPER_WINDOW_PRESET: DeveloperWindowPresetId = '30d'

const GRANULARITIES: readonly string[] = ACTIVITY_GRANULARITIES

/** Whether a `?range=` value names a preset this surface offers. */
export function isDeveloperWindowPreset(
  value: string | null | undefined,
): value is DeveloperWindowPresetId {
  return value !== null && DEVELOPER_WINDOW_PRESETS.some((preset) => preset.id === value)
}

/** Clamps a span into what the endpoints will accept. Zero is not a window. */
function clampWindowDays(days: number): number {
  return Math.min(Math.max(Math.round(days), 1), MAX_DEVELOPER_WINDOW_DAYS)
}

export interface DeveloperWindow {
  preset: DeveloperWindowPresetId
  /**
   * The trailing span in days, or `undefined` for the default preset.
   *
   * `undefined` means "ask the backend for its own default" — not "zero days"
   * and not "the default is 30". `DeveloperSummaryRead.window_days` echoes what
   * the server actually used, so a caption quotes that rather than this.
   */
  window_days: number | undefined
  granularity: ActivityGranularity
  /**
   * The custom range, always resolved — `?start`/`?end` when present, otherwise
   * the last `DEVELOPER_DEFAULT_WINDOW_DAYS` days — so a picker can render its
   * inputs before the user has chosen one, and switching back to Custom restores
   * what they chose rather than resetting it.
   */
  custom: { start: DateOnlyString; end: DateOnlyString } | null
  /** Ready-made request parameters for every window-shaped read. */
  params: ActivityParams
  setPreset: (preset: DeveloperWindowPresetId) => void
  setCustom: (start: DateOnlyString, end: DateOnlyString) => void
  setGranularity: (granularity: ActivityGranularity) => void
}

/**
 * The developer window, held in the URL.
 *
 * **Shareable, not remembered.** `?range=90d` means the same thing to whoever
 * receives the link, and the browser's back button steps through windows instead
 * of out of the page. A param equal to its default is *deleted* rather than
 * written, so the common case stays a clean URL and two people looking at the
 * same view produce the same string.
 *
 * **A custom window contributes its length, not its position.** The endpoints
 * resolve `window_days` backwards from today, so `?start=2026-01-01&end=2026-01-31`
 * is sent as `window_days: 31`. The range is retained in `custom` so the picker
 * can show what was chosen and restore it; it is not sent, because there is no
 * parameter to send it in and inventing one would silently do nothing.
 *
 * **`params` is memoised** and contains only normalised fields, so it can be
 * spread straight into a query hook without changing the hash of the key.
 */
export function useDeveloperWindow(
  defaultPreset: DeveloperWindowPresetId = DEFAULT_DEVELOPER_WINDOW_PRESET,
): DeveloperWindow {
  const [searchParams, setSearchParams] = useSearchParams()

  const rangeParam = searchParams.get('range')
  const preset: DeveloperWindowPresetId = isDeveloperWindowPreset(rangeParam)
    ? rangeParam
    : defaultPreset

  const startParam = searchParams.get('start')
  const endParam = searchParams.get('end')

  const custom = useMemo(() => {
    const today = todayDateOnly()
    const start = isDateOnly(startParam)
      ? startParam
      : shiftDays(today, -(DEVELOPER_DEFAULT_WINDOW_DAYS - 1))
    const end = isDateOnly(endParam) ? endParam : today
    // An inverted custom window is a 422 server-side; clamp rather than render
    // a screen that can only ever show an error.
    return start <= end ? { start, end } : { start: end, end: start }
  }, [startParam, endParam])

  const granularityParam = searchParams.get('granularity')
  const granularity: ActivityGranularity = GRANULARITIES.includes(granularityParam ?? '')
    ? (granularityParam as ActivityGranularity)
    : DEVELOPER_DEFAULT_ACTIVITY_GRANULARITY

  const windowDays = useMemo<number | undefined>(() => {
    // The default preset sends nothing at all and lets the backend decide.
    if (preset === defaultPreset) return undefined
    if (preset === 'custom') {
      const days = rangeDays(custom.start, custom.end)
      return days > 0 ? clampWindowDays(days) : undefined
    }
    const days = DEVELOPER_WINDOW_PRESETS.find((entry) => entry.id === preset)?.days
    return days === undefined ? undefined : clampWindowDays(days)
  }, [preset, custom, defaultPreset])

  const params = useMemo<ActivityParams>(
    () => ({ window_days: windowDays, granularity }),
    [windowDays, granularity],
  )

  const write = useCallback(
    (next: {
      preset: DeveloperWindowPresetId
      start?: DateOnlyString
      end?: DateOnlyString
      granularity?: ActivityGranularity
    }) => {
      const nextParams = new URLSearchParams(searchParams)
      if (next.preset === defaultPreset) nextParams.delete('range')
      else nextParams.set('range', next.preset)
      if (next.start && next.end) {
        nextParams.set('start', next.start)
        nextParams.set('end', next.end)
      } else {
        nextParams.delete('start')
        nextParams.delete('end')
      }
      if (next.granularity && next.granularity !== DEVELOPER_DEFAULT_ACTIVITY_GRANULARITY) {
        nextParams.set('granularity', next.granularity)
      } else {
        nextParams.delete('granularity')
      }
      // `replace` so stepping through windows does not fill the history with
      // states a back button has to walk back through one range at a time.
      setSearchParams(nextParams, { replace: true })
    },
    [searchParams, setSearchParams, defaultPreset],
  )

  return {
    preset,
    window_days: windowDays,
    granularity,
    custom,
    params,
    // The grain is carried through every setter, so changing the range does not
    // silently re-bucket the chart: switching to 90 days should not also undo
    // a week grain the user had already chosen. Only `setGranularity` changes it.
    setPreset: (next) => write({ preset: next, granularity }),
    setCustom: (start, end) => write({ preset: 'custom', start, end, granularity }),
    setGranularity: (next) => write({ preset, granularity: next }),
  }
}

/* ----------------------------------------------------------------- queries */

/**
 * The dashboard's single request: counts, the sentence describing them and the
 * window they were computed over.
 *
 * One round trip, so the header tiles and the sentence beneath them cannot
 * quote different totals. An account with nothing registered answers with real
 * zeroes *and* `has_data: false` — the counts are honest, and the flag is what
 * turns them into an empty state to explain rather than a finding.
 *
 * `placeholderData` keeps the previous window on screen while the next one
 * loads; a query reading `isPlaceholderData` can say the figures are the
 * previous window's.
 */
export function useDeveloperSummary(
  params: DeveloperWindowParams = {},
  options: Enabled = {},
): UseQueryResult<DeveloperSummaryRead> {
  return useQuery({
    queryKey: developerKeys.summary(params),
    queryFn: ({ signal }) => fetchDeveloperSummary(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * All eight metrics, each with its definition, its explanation and its own
 * availability.
 *
 * The endpoint always returns all eight, marking the ones the data could not
 * support with `available: false` — so nothing here has to guard for a metric
 * going missing, and `metricUnavailableReason` in `./format` renders the reason.
 */
export function useDeveloperMetrics(
  params: DeveloperWindowParams = {},
  options: Enabled = {},
): UseQueryResult<DeveloperMetricRead[]> {
  return useQuery({
    queryKey: developerKeys.metrics(params),
    queryFn: ({ signal }) => fetchDeveloperMetrics(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * The zero-filled activity series, optionally narrowed to one repository.
 *
 * `repository_id` is part of the key because "everything" and "this repository"
 * are different series and must never share a cache entry. The buckets are
 * dense on the wire — a quiet day is present with `commits: 0` — so a chart can
 * plot them as they are without re-implementing the fill.
 */
export function useDeveloperActivity(
  params: ActivityParams = {},
  options: Enabled = {},
): UseQueryResult<DeveloperActivityRead> {
  return useQuery({
    queryKey: developerKeys.activity(params),
    queryFn: ({ signal }) => fetchDeveloperActivity(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * The commit timeline across every repository the account owns.
 *
 * `repository_id` and `branch` are server-side filters, so a filtered timeline
 * and an unfiltered one are separate cache entries and separate answers. This
 * is the "what happened anywhere" view; the per-repository history is
 * {@link useRepositoryCommits}.
 */
export function useDeveloperCommits(
  params: CommitListParams = {},
  options: Enabled = {},
): UseQueryResult<CommitListRead> {
  return useQuery({
    queryKey: developerKeys.commits(params),
    queryFn: ({ signal }) => fetchDeveloperCommits(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * Registered repositories, newest first.
 *
 * `is_active` and `project_id` are served from indexes server-side, so the
 * filtering happens in one query and `total` describes the filtered set rather
 * than one page of it.
 */
export function useRepositories(
  params: RepositoryListParams = {},
  options: Enabled = {},
): UseQueryResult<RepositoryListRead> {
  return useQuery({
    queryKey: developerKeys.repositories(params),
    queryFn: ({ signal }) => fetchRepositories(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * One repository in detail, including the outcome of its last scan.
 *
 * Disabled while there is no id, so a detail route that renders before its
 * parameter is parsed does not request `/developer/repositories/undefined`.
 * Someone else's id is a 404 and never a 403; ownership is the server's alone
 * and this surface is not told the difference.
 *
 * A repository whose last scan failed is still a 200 here, carrying
 * `last_scan_status: 'error'` and a sentence in `last_scan_error`.
 */
export function useRepository(
  id: UUIDString | null | undefined,
  options: Enabled = {},
): UseQueryResult<RepositoryRead> {
  return useQuery({
    queryKey: developerKeys.repository(id ?? ''),
    queryFn: ({ signal }) => fetchRepository(id as UUIDString, signal),
    enabled: (options.enabled ?? true) && Boolean(id),
  })
}

/** One repository's recorded history. Disabled while there is no id. */
export function useRepositoryCommits(
  id: UUIDString | null | undefined,
  params: CommitListParams = {},
  options: Enabled = {},
): UseQueryResult<CommitListRead> {
  return useQuery({
    queryKey: developerKeys.repositoryCommits(id ?? '', params),
    queryFn: ({ signal }) => fetchRepositoryCommits(id as UUIDString, params, signal),
    enabled: (options.enabled ?? true) && Boolean(id),
    placeholderData: (previous) => previous,
  })
}

/** The branches the last scan observed. Disabled while there is no id. */
export function useRepositoryBranches(
  id: UUIDString | null | undefined,
  params: PaginationParams = {},
  options: Enabled = {},
): UseQueryResult<BranchListRead> {
  return useQuery({
    queryKey: developerKeys.repositoryBranches(id ?? '', params),
    queryFn: ({ signal }) => fetchRepositoryBranches(id as UUIDString, params, signal),
    enabled: (options.enabled ?? true) && Boolean(id),
    placeholderData: (previous) => previous,
  })
}

/* --------------------------------------------------------------- mutations */

/**
 * Registers a local repository.
 *
 * The backend resolves the path and proves it is a git work tree before storing
 * anything, so a bad path is a 422 with a sentence and no row is created. A
 * bare `git init` with no commits is valid and registers fine.
 *
 * Invalidation is the whole tree: a new repository changes the repository count
 * in the summary, may add branches and commits on its first scan, and therefore
 * moves every metric and the activity series.
 */
export function useCreateRepository(): UseMutationResult<
  RepositoryRead,
  Error,
  RepositoryCreatePayload
> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: RepositoryCreatePayload) => createRepository(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: developerKeys.all() })
    },
  })
}

/**
 * Removes a repository and, by cascade, its commits, branches and scan runs.
 *
 * The recorded history goes with it — there is no archive flag on this surface —
 * so the account's activity trail still shows that it happened while the
 * evidence does not linger. Invalidation covers the summary, which counted it.
 */
export function useDeleteRepository(): UseMutationResult<void, Error, UUIDString> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUIDString) => deleteRepository(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: developerKeys.all() })
    },
  })
}

/**
 * Rescans a repository now, synchronously, and reports what the attempt did.
 *
 * A mutation and a deliberately blocking one: there is no background scheduler
 * in NEXUS and Phase 8 adds none, so this call *is* the scan.
 *
 * **A failure resolves rather than rejects.** `status: 'error'` with a human
 * sentence is a 200, which is what keeps one unreadable directory from taking
 * the page down; the caller reads `status` and renders `error`. Re-reading is
 * idempotent — commits are upserted on `(repository_id, commit_hash)`, so
 * `commits_discovered` exceeding `commits_added` on a second scan is the
 * deduplication working. Omitting `params` asks for the incremental default, in
 * which case only commits the scan has not seen are transferred.
 *
 * The caller re-reads the repository afterwards: the run says what the attempt
 * did, not what the row now looks like.
 */
export function useScanRepository(): UseMutationResult<
  ScanRunRead,
  Error,
  { id: UUIDString; params?: RepositoryScanParams }
> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      params,
    }: {
      id: UUIDString
      params?: RepositoryScanParams
    }) => scanRepository(id, params ?? {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: developerKeys.all() })
    },
  })
}

/**
 * The page size the sweep reads, which is the endpoint's own ceiling and not
 * this surface's grid size.
 *
 * `MAX_PAGE_SIZE` in `app/api/v1/developer.py` is 200 and the repository layer
 * clamps to the same number, so anything above it is a 422 rather than a quiet
 * truncation. Reusing the dashboard's twelve here would be a bug wearing a
 * constant's clothes: an account with thirty repositories would be told it had
 * synced all of them while twelve were scanned.
 *
 * `offset` is pinned to zero and the list is **not** paged through. Walking it
 * would mean one request per page of ids before the first scan started, and the
 * sweep is already a series of git processes; a single read that either covers
 * the account or is visibly short of it is the honest shape. When `total`
 * exceeds what came back, the caller says so rather than reporting a full sweep.
 */
const SYNC_ALL_PAGE_LIMIT = 200

/**
 * What one sweep of every active repository did.
 *
 * Data, not a sentence: the caller composes the message, because only the caller
 * knows what a reader should be told about a partial sweep. `read` and `total`
 * are both carried so a clipped page can be reported as clipped — `synced` alone
 * would read as "this is everything" whether or not it is.
 */
export interface ScanAllRepositoriesResult {
  /** Repositories the single bounded read returned. */
  read: number
  /** Repositories matching the filter, which may be more than came back. */
  total: number
  /** Repositories whose scan finished without an error. */
  synced: number
  /** Commits written across those repositories. Lower than "discovered" is normal. */
  commitsAdded: number
  /** The repositories git could not read, each with the sentence it gave. */
  failures: { id: UUIDString; name: string; reason: string }[]
}

/**
 * Rescans **every** active repository in series and reports what the sweep did.
 *
 * **One bounded read, not the page on screen.** The dashboard's grid shows twelve
 * of them, so "sync everything I own" cannot be answered from those rows: it
 * takes one `GET /developer/repositories` at the endpoint's ceiling with
 * `is_active`, which is served from the owner/active index, and this hook asks
 * for no repository the caller does not own.
 *
 * **In series, never in parallel.** Every one of these is a `git` process on the
 * machine NEXUS runs on. Twelve at once would contend for the same working
 * trees, the same index locks and the same CPU the reader's editor is using; in
 * series each scan gets the machine to itself, and a failure costs one
 * repository's turn rather than the whole sweep.
 *
 * **A failure is collected, never raised.** `status: 'error'` is a 200 with a
 * human sentence, and a transport failure throws — both land in `failures` and
 * the sweep continues, which is the same promise the rest of this surface makes
 * about a broken repository, applied to the whole run at once. The only rejection
 * this mutation can produce is the initial list read, where there is no sweep to
 * report on at all.
 *
 * **Invalidation is once, at the end.** `useScanRepository` invalidates the tree
 * per scan; doing that inside the loop would re-read the dashboard's six queries
 * between every repository. One invalidation after the last scan writes the same
 * state, and `isPending` spans the whole sweep — which is what lets the button
 * show a single honest busy state rather than one flicker per repository.
 */
export function useScanAllRepositories(): UseMutationResult<
  ScanAllRepositoriesResult,
  Error,
  void
> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (): Promise<ScanAllRepositoriesResult> => {
      const list = await fetchRepositories({
        limit: SYNC_ALL_PAGE_LIMIT,
        offset: 0,
        is_active: true,
      })

      const failures: ScanAllRepositoriesResult['failures'] = []
      let synced = 0
      let commitsAdded = 0

      for (const repository of list.items) {
        try {
          const run = await scanRepository(repository.id)
          if (run.status === 'error') {
            failures.push({
              id: repository.id,
              name: repository.name,
              reason: run.error ?? 'The scan reported a failure without saying why.',
            })
            continue
          }
          synced += 1
          commitsAdded += run.commits_added
        } catch (cause) {
          failures.push({
            id: repository.id,
            name: repository.name,
            reason: toApiError(cause).message,
          })
        }
      }

      return { read: list.items.length, total: list.total, synced, commitsAdded, failures }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: developerKeys.all() })
    },
  })
}