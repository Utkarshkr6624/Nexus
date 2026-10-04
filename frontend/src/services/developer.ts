/**
 * Thin typed wrappers over the Phase 8 developer intelligence endpoints.
 *
 * No React here — every function is a promise-returning call the hooks in
 * `features/developer/hooks.ts` wrap in a `queryFn`/`mutationFn`.
 *
 * Six decisions are dictated by the backend rather than chosen here, and each
 * of them exists because the alternative would let a client build a request the
 * server will refuse or misread:
 *
 * - **Literal sub-paths are declared before `/{repository_id}`** on the router,
 *   and `DEVELOPER_ENDPOINTS` keeps the same shape: `/developer/summary`,
 *   `/metrics`, `/activity`, `/commits` and `/features` are plain strings, and
 *   only the paths that genuinely carry an id are arrow functions. One map, one
 *   ordering, and the id-bearing keys are unambiguous by their own type.
 * - **Every function is `(params = {}, signal?: AbortSignal)`.** `params`
 *   defaults to `{}` because an omitted `window_days` is not the same request as
 *   `window_days: 30`: the first asks the backend for its own configured
 *   default, and the second pins a range that may not match what the server
 *   would have chosen. `queryFrom` therefore drops `undefined`, `null` and `''`
 *   rather than serialising them, and skips arrays outright — `QueryParams`
 *   carries one scalar per key and its serialiser emits `?key=value`, never a
 *   repeated key, so an array would be dropped silently and the screen would
 *   show a wider result set than the user asked for.
 * - **Scanning is a `POST`, not a `GET`.** It writes commits, branches and a
 *   scan run, and emits `REPOSITORY_SCANNED`, so it must never be cached as
 *   though it were a read or pre-fetched by a link hover.
 * - **`POST /developer/repositories` validates the path server-side** before
 *   storing it, so this module never inspects the filesystem and cannot report a
 *   path as valid on the strength of its own guess.
 * - **`PATCH` carries no `local_path`.** The path is the row's identity and the
 *   one field checked against the disk, so it is omitted from the update
 *   payload and no caller can even express the move. The same omission applies
 *   to `primary_language`, which the scan measures.
 * - **Scanning returns {@link ScanRunRead}, not {@link RepositoryRead}.** The
 *   run is the record of what the attempt did — including a failure, which is
 *   the only shape in which "this repository could not be read" reaches the
 *   client. The refreshed repository comes from the repository queries, so a
 *   caller re-reads it rather than trusting an optimistic patch.
 *
 * Ownership is the server's alone: every route filters on `user_id`, so another
 * account's repository is a 404 and never a 403, and this module has no notion
 * of whose rows it is asking for.
 *
 * Failures are not handled here. `apiClient` throws `ApiError` with the status,
 * the machine-readable code and any field-level details, and the hooks decide
 * what a 404 means for the screen. Note that a *failed scan is not an error
 * response*: it is a 200 carrying `status: 'error'` and a human sentence, which
 * is the mechanism behind "a broken repository must never break NEXUS".
 */
import { apiClient, queryFrom } from '@/lib/api-client'
import type {
  ActivityParams,
  BranchListRead,
  CommitListParams,
  CommitListRead,
  DeveloperActivityRead,
  DeveloperMetricRead,
  DeveloperSummaryRead,
  DeveloperWindowParams,
  PaginationParams,
  RepositoryCreatePayload,
  RepositoryListParams,
  RepositoryListRead,
  RepositoryRead,
  RepositoryScanParams,
  ScanRunRead,
  UUIDString,
} from '@/types/developer'

export const DEVELOPER_ENDPOINTS = {
  summary: '/developer/summary',
  metrics: '/developer/metrics',
  activity: '/developer/activity',
  commits: '/developer/commits',
  features: '/developer/features',
  repositories: '/developer/repositories',
  repository: (id: UUIDString) => `/developer/repositories/${id}`,
  repositoryScan: (id: UUIDString) => `/developer/repositories/${id}/scan`,
  repositoryCommits: (id: UUIDString) => `/developer/repositories/${id}/commits`,
  repositoryBranches: (id: UUIDString) => `/developer/repositories/${id}/branches`,
  project: (id: UUIDString) => `/developer/projects/${id}`,
} as const

/**
 * Builds a query object, dropping anything unset.
 *
 * Typed as `object` rather than `Record<string, unknown>` because an interface
 * carries no implicit index signature and would not be assignable to that record
 * — the same reason `services/risk.ts` does it this way.
 */
/* ------------------------------------------------------------------ dashboard */

/**
 * The account-wide headline figures, plus the window they were computed over.
 *
 * A single round trip for the whole overview: the counts and the sentence
 * describing them come from one response so the header and the tiles beneath it
 * cannot quote different totals. Returns all-zero counts with `has_data: false`
 * on an account with nothing registered — an empty state to explain, not an
 * error, and the reason the flags exist.
 *
 * `window_days` is optional because the backend owns the default; sending
 * nothing asks for the server's own window rather than one invented here.
 */
export function fetchDeveloperSummary(
  params: DeveloperWindowParams = {},
  signal?: AbortSignal,
): Promise<DeveloperSummaryRead> {
  return apiClient.get<DeveloperSummaryRead>(DEVELOPER_ENDPOINTS.summary, {
    query: queryFrom({ window_days: params.window_days }),
    signal,
  })
}

/**
 * All eight metrics, each with its definition and its explanation.
 *
 * Always eight elements, never fewer: a metric the data could not support is
 * returned with `available: false` and a reason rather than dropped, so a client
 * renders "Not enough data yet." instead of quietly losing a card. The array is
 * a plain list rather than a keyed object so the server's ordering survives and
 * a chart legend cannot rearrange itself between renders.
 */
export function fetchDeveloperMetrics(
  params: DeveloperWindowParams = {},
  signal?: AbortSignal,
): Promise<DeveloperMetricRead[]> {
  return apiClient.get<DeveloperMetricRead[]>(DEVELOPER_ENDPOINTS.metrics, {
    query: queryFrom({ window_days: params.window_days }),
    signal,
  })
}

/**
 * Commits bucketed day, week or month, with the gaps zero-filled.
 *
 * `granularity` omitted asks the server for `developer_activity_granularity_default`,
 * and `repository_id` narrows the series to one repository. Buckets are dense:
 * a quiet Tuesday arrives with `commits: 0` rather than being skipped, because
 * an omitted bucket silently compresses the timeline.
 */
export function fetchDeveloperActivity(
  params: ActivityParams = {},
  signal?: AbortSignal,
): Promise<DeveloperActivityRead> {
  return apiClient.get<DeveloperActivityRead>(DEVELOPER_ENDPOINTS.activity, {
    query: queryFrom({
      window_days: params.window_days,
      granularity: params.granularity,
      repository_id: params.repository_id,
    }),
    signal,
  })
}

/**
 * The commit timeline across every repository the account owns.
 *
 * Distinct from the per-repository commits route on purpose: the global one is
 * the "what happened anywhere" view, and folding it into a per-repository call
 * would force the dashboard to know which repository to ask about.
 */
export function fetchDeveloperCommits(
  params: CommitListParams = {},
  signal?: AbortSignal,
): Promise<CommitListRead> {
  return apiClient.get<CommitListRead>(DEVELOPER_ENDPOINTS.commits, {
    query: queryFrom({
      limit: params.limit,
      offset: params.offset,
      repository_id: params.repository_id,
      branch: params.branch,
    }),
    signal,
  })
}

/* -------------------------------------------------------------- repositories */

/** Registered repositories, newest first, filtered and paginated. */
export function fetchRepositories(
  params: RepositoryListParams = {},
  signal?: AbortSignal,
): Promise<RepositoryListRead> {
  return apiClient.get<RepositoryListRead>(DEVELOPER_ENDPOINTS.repositories, {
    query: queryFrom({
      limit: params.limit,
      offset: params.offset,
      is_active: params.is_active,
      project_id: params.project_id,
    }),
    signal,
  })
}

/**
 * Registers a local repository.
 *
 * The backend resolves the path and proves it is a git work tree before storing
 * anything, so a bad path is a 422 carrying a sentence and no row is created —
 * this client does no filesystem check of its own and cannot report a path as
 * valid on a guess. Registration is capped per account and emits
 * `REPOSITORY_REGISTERED`.
 *
 * A mutation: it writes a row and an activity event, so its result must never
 * be cached as though it were a read.
 */
export function createRepository(payload: RepositoryCreatePayload): Promise<RepositoryRead> {
  return apiClient.post<RepositoryRead>(DEVELOPER_ENDPOINTS.repositories, payload)
}

/**
 * One repository in detail, including its last scan outcome.
 *
 * Someone else's id is a 404 here, not a 403 — ownership is server-side and the
 * client is not told the difference. A repository whose last scan failed is
 * still a 200 here, carrying `last_scan_status: 'error'` and a sentence in
 * `last_scan_error`; that pair, not an exception, is how a broken repository
 * reaches the page.
 */
export function fetchRepository(
  id: UUIDString,
  signal?: AbortSignal,
): Promise<RepositoryRead> {
  return apiClient.get<RepositoryRead>(DEVELOPER_ENDPOINTS.repository(id), { signal })
}

/**
 * Removes a repository and, by cascade, its commits, branches and scan runs.
 *
 * The history goes with it. There is no archive flag here and no soft delete:
 * a repository the user no longer wants is removed with the evidence gathered
 * from it, and the scan runs that recorded why are removed with the rest. The
 * activity event survives, so the account's own trail still shows it happened.
 */
export function deleteRepository(id: UUIDString): Promise<void> {
  return apiClient.delete<void>(DEVELOPER_ENDPOINTS.repository(id))
}

/**
 * Rescans a repository now, synchronously, and reports what the attempt did.
 *
 * A mutation, and a deliberately blocking one: there is no background scheduler
 * in NEXUS and Phase 8 does not add one, so this call is the scan. It answers
 * {@link ScanRunRead} rather than {@link RepositoryRead} because the run is the
 * record of the *attempt* — it is the only shape in which a failure arrives, and
 * `status: 'error'` with a human sentence is a 200, not a rejection.
 *
 * Re-reading is idempotent: commits are upserted on
 * `(repository_id, commit_hash)`, so `commits_discovered` exceeding
 * `commits_added` on a second scan is the deduplication working. `full` is
 * optional and off by default, in which case the backend passes `--since` the
 * repository's `latest_commit_at` and transfers only commits it has not seen.
 *
 * Callers re-read the repository afterwards: the run tells you what happened,
 * not what the row now looks like.
 */
export function scanRepository(
  id: UUIDString,
  params: RepositoryScanParams = {},
): Promise<ScanRunRead> {
  return apiClient.post<ScanRunRead>(
    DEVELOPER_ENDPOINTS.repositoryScan(id),
    undefined,
    { query: queryFrom({ full: params.full }) },
  )
}

/**
 * One repository's recorded history, newest first.
 *
 * Separate from the global timeline rather than the same call with an id: the
 * route already implies the repository, so passing one here would be a second
 * answer to a question the path has already settled.
 */
export function fetchRepositoryCommits(
  id: UUIDString,
  params: CommitListParams = {},
  signal?: AbortSignal,
): Promise<CommitListRead> {
  return apiClient.get<CommitListRead>(DEVELOPER_ENDPOINTS.repositoryCommits(id), {
    query: queryFrom({ limit: params.limit, offset: params.offset, branch: params.branch }),
    signal,
  })
}

/**
 * The branches the last scan observed.
 *
 * Attributed from a single `for-each-ref` pass, so `head_commit_hash` and
 * `last_committed_at` are what git reported rather than anything inferred. On a
 * detached HEAD no branch is `is_current`, which is a normal state.
 */
export function fetchRepositoryBranches(
  id: UUIDString,
  params: PaginationParams = {},
  signal?: AbortSignal,
): Promise<BranchListRead> {
  return apiClient.get<BranchListRead>(DEVELOPER_ENDPOINTS.repositoryBranches(id), {
    query: queryFrom({ limit: params.limit, offset: params.offset }),
    signal,
  })
}

/* ------------------------------------------------------------------- project */

