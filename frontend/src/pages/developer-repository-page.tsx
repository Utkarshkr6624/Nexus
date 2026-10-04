import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { FolderGit2, FolderX, Languages, Trash2 } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatNumber } from '@/features/analytics/format'
import {
  BranchList,
  CommitTimeline,
  DEFAULT_STALE_HOURS,
  DeveloperActivitySection,
  LanguageBadge,
  NeverScannedHint,
  ScanRunList,
  ScanStatusBadge,
  ScanStatusPanel,
  TimelineScopeNote,
  describeCurrentBranch,
  formatDeveloperInstant,
  formatScanAge,
  isScanStale,
} from '@/features/developer/components'
import {
  useDeleteRepository,
  useDeveloperActivity,
  useDeveloperWindow,
  useRepository,
  useRepositoryBranches,
  useRepositoryCommits,
  useScanRepository,
} from '@/features/developer/hooks'
import { ConfirmDialog } from '@/features/work/components/confirm-dialog'
import { useProject } from '@/features/work/hooks'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import type { CommitRead, RepositoryRead, ScanRunRead, UUIDString } from '@/types/developer'

/**
 * One repository: what git recorded in it, what its last scan did, and the
 * project it belongs to.
 *
 * ## The route is a placeholder for an id that may not be yours
 *
 * `useRepository` is disabled until `:repositoryId` parses, and another
 * account's repository is a **404** — never a 403, because ownership is the
 * server's alone and this surface is not told which of the two it hit. A 404 is
 * therefore an answer rather than a failure, and it gets its own screen with a
 * way back instead of a retry button that could never succeed.
 *
 * ## Scanning is the one mutation on this page, and it is deliberately blocking
 *
 * There is no background scheduler in NEXUS and Phase 8 adds none, so the scan
 * button *is* the scan. It answers 200 whether or not git could read the
 * directory: a failure arrives as `status: 'error'` with a human sentence and is
 * rendered as a state by `ScanStatusPanel`, which is the whole mechanism behind
 * "a broken repository must never break NEXUS". There is no exception path here
 * for a bad repository to travel down.
 *
 * Re-scanning is idempotent — commits are upserted on
 * `(repository_id, commit_hash)` — so `commits_discovered` exceeding
 * `commits_added` on a second scan is the deduplication working, and the run
 * record says so rather than letting a reader read it as data loss.
 *
 * ## What the page is allowed to say
 *
 * Counts, branches, diff sizes and scan durations — nothing about a person. In
 * particular the commit list is shown literally: no grouping into sessions, no
 * streaks, no "you were active for three hours". A commit timestamp records that
 * work happened at an instant and nothing about its length, so a figure for the
 * latter does not exist on this page.
 *
 * **Nothing here re-reads the repository on its own.** A scan only runs when the
 * reader asks for one, so every figure is as of the last scan, and the page says
 * how old that is rather than presenting it as current.
 */
const COMMIT_PAGE_LIMIT = 50
const BRANCH_PAGE_LIMIT = 100

const NO_COMMITS: CommitRead[] = []

export default function DeveloperRepositoryPage() {
  const { repositoryId } = useParams<{ repositoryId: string }>()
  const navigate = useNavigate()
  const window = useDeveloperWindow()

  const repository = useRepository(repositoryId)
  const activity = useDeveloperActivity({
    ...window.params,
    repository_id: repositoryId ?? undefined,
  })
  const commits = useRepositoryCommits(repositoryId, { limit: COMMIT_PAGE_LIMIT, offset: 0 })
  const branches = useRepositoryBranches(repositoryId, { limit: BRANCH_PAGE_LIMIT, offset: 0 })

  const record = repository.data
  const project = useProject(record?.project_id)

  const scan = useScanRepository()
  const remove = useDeleteRepository()
  const [lastRun, setLastRun] = useState<ScanRunRead | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)

  /**
   * The window's change figures.
   *
   * Summed from the buckets the activity endpoint returned for **this**
   * repository, which are dense and zero-filled, so the totals cover the whole
   * window rather than the days that happened to carry a commit. A `null` before
   * the series arrives renders as a dash rather than a zero — "no measurement
   * yet" and "nothing changed" are different facts.
   *
   * Declared before the early returns below so the hook order is stable across
   * the loading, error and loaded branches.
   */
  const changeTotals = useMemo(() => {
    if (!activity.data) return null
    // `buckets` is required by the wire type, but a degraded or partially cached
    // response should blank one total rather than crash the page into the route
    // error boundary. The zero-filled default keeps every consumer on the same
    // "no measurement" path it already handles.
    return (activity.data.buckets ?? []).reduce(
      (sum, bucket) => ({
        additions: sum.additions + bucket.additions,
        deletions: sum.deletions + bucket.deletions,
        files: sum.files + bucket.files_changed,
      }),
      { additions: 0, deletions: 0, files: 0 },
    )
  }, [activity.data])

  const runScan = useCallback(() => {
    if (!repositoryId) return
    scan.mutate(
      { id: repositoryId },
      {
        onSuccess: (run) => {
          setLastRun(run)
          if (run.status === 'error') {
            // A failed scan is data, not an exception: the repository is still
            // registered and its recorded commits are untouched, and the panel
            // below carries the sentence explaining why it could not be read.
            toast.error('The scan could not read this repository', run.error ?? undefined)
            return
          }
          toast.success(
            'Scan complete',
            `${formatNumber(run.commits_discovered)} commits discovered, ` +
              `${formatNumber(run.commits_added)} new, ` +
              `${formatNumber(run.branches_discovered)} branches seen, in ${formatNumber(run.duration_ms)} ms.`,
          )
        },
        onError: (cause) => toast.error('The scan did not complete', toApiError(cause).message),
      },
    )
  }, [repositoryId, scan])

  const onDelete = useCallback(() => {
    if (!repositoryId) return
    remove.mutate(repositoryId, {
      onSuccess: () => {
        toast.success(
          'Repository removed',
          'Its recorded commits, branches and scan record went with it.',
        )
        navigate('/developer', { replace: true })
      },
      onError: (cause) => toast.error('Could not remove that repository', toApiError(cause).message),
    })
  }, [repositoryId, remove, navigate])

  if (repository.isPending) return <RepositoryDetailSkeleton />

  if (repository.isError) {
    const error = toApiError(repository.error)
    // A 404 is an answer, not a failure: the row is gone, or it was never this
    // account's. Both are reported identically, so there is nothing further to
    // check and a retry button would be a lie.
    if (error.isNotFound) {
      return (
        <div className="app-container py-6 lg:py-8">
          <EmptyState
            icon={FolderX}
            title="That repository is not here"
            description="It may have been removed, or it may belong to another account. Both answer the same way, so there is nothing further to check."
            action={
              <Button asChild variant="outline">
                <Link to="/developer">Back to developer intelligence</Link>
              </Button>
            }
          />
        </div>
      )
    }
    return (
      <div className="app-container py-6 lg:py-8">
        <ErrorState error={error} onRetry={() => void repository.refetch()} />
      </div>
    )
  }

  if (!record) {
    return (
      <div className="app-container py-6 lg:py-8">
        <EmptyState
          icon={FolderX}
          title="No repository to show"
          description="The address did not carry a repository id, so there is nothing to read."
          action={
            <Button asChild variant="outline">
              <Link to="/developer">Back to developer intelligence</Link>
            </Button>
          }
        />
      </div>
    )
  }

  const neverScanned = record.last_scan_status === null
  const stale = isScanStale(record.last_scanned_at, DEFAULT_STALE_HOURS)
  const commitRows = commits.data?.items ?? NO_COMMITS
  const branchRows = branches.data?.items ?? []

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title={record.name}
        eyebrow={
          <Link to="/developer" className="hover:text-foreground">
            Developer
          </Link>
        }
        badges={
          <>
            <ScanStatusBadge status={record.last_scan_status} />
            <LanguageBadge language={record.primary_language} />
            {record.is_active ? (
              <Badge variant="secondary">Active</Badge>
            ) : (
              <Badge variant="secondary">Marked inactive</Badge>
            )}
          </>
        }
        actions={
          <Button
            type="button"
            variant="outline"
            className="text-muted-foreground hover:text-destructive"
            onClick={() => setConfirmOpen(true)}
            disabled={remove.isPending}
          >
            <Trash2 aria-hidden="true" />
            Remove repository
          </Button>
        }
        description={record.description ?? undefined}
      />

      {neverScanned && <NeverScannedHint />}

      <div className="grid gap-4 lg:grid-cols-3">
        <RepositoryFacts repository={record} isStale={stale} />
        <ScanStatusPanel
          status={record.last_scan_status}
          error={record.last_scan_error}
          lastScannedAt={record.last_scanned_at}
          commitCount={record.commit_count}
          isScanning={scan.isPending}
          onScan={runScan}
        />
        <ProjectConnection
          projectId={record.project_id}
          projectName={project.data?.name ?? null}
          isLoading={record.project_id !== null && project.isPending}
          isError={record.project_id !== null && project.isError}
        />
      </div>

      {lastRun && (
        <section className="space-y-3" aria-labelledby="developer-last-run">
          <h2 id="developer-last-run" className="sr-only">
            Latest scan attempt
          </h2>
          <ScanRunList
            runs={[lastRun]}
            title="Scan you just ran"
            titleLevel="h3"
          />
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <DeveloperActivitySection
            activity={activity.data ?? null}
            isLoading={activity.isPending && !activity.data}
            isStale={activity.isPlaceholderData || (activity.isFetching && !activity.isPending)}
            granularity={window.granularity}
            onGranularityChange={window.setGranularity}
            scopeLabel={record.name}
            title="Recorded activity in this repository"
          />
        </div>

        <Card className="min-w-0">
          <CardHeader className="pb-3">
            <CardTitle>Change statistics</CardTitle>
            <CardDescription>
              Summed across the {(activity.data?.buckets ?? []).length} {window.granularity} buckets of
              the current window. A file edited in three commits is counted three times, because
              git recorded three changes to it.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {activity.isPending && !changeTotals ? (
              <div className="space-y-3" role="status" aria-busy="true">
                <span className="sr-only">Loading the change statistics</span>
                <Skeleton className="h-7 w-24" />
                <Skeleton className="h-7 w-24" />
                <Skeleton className="h-7 w-24" />
              </div>
            ) : (
              <dl className="space-y-3">
                <Fact label="Files changed" value={changeTotals?.files ?? null} />
                <Fact label="Lines added" value={changeTotals?.additions ?? null} />
                <Fact label="Lines removed" value={changeTotals?.deletions ?? null} />
                <Fact label="Commits, whole history" value={record.commit_count} />
              </dl>
            )}
            <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
              These are counts of lines in a diff. They say nothing about how the change was made or
              how long it took.
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <CommitHistory
          commits={commitRows}
          total={commits.data?.total ?? 0}
          isLoading={commits.isPending && !commits.data}
          isStale={commits.isPlaceholderData}
          error={commits.isError && !commits.isPlaceholderData ? commits.error : null}
          onRetry={() => void commits.refetch()}
        />

        <div className="min-w-0 space-y-4">
          <section className="space-y-3" aria-labelledby="developer-branches">
            <h2 id="developer-branches" className="sr-only">
              Branches
            </h2>
            <BranchList
              branches={branchRows}
              isLoading={branches.isPending && !branches.data}
              isStale={branches.isPlaceholderData}
              titleLevel="h3"
              subtitle="The branches the last scan observed. Attribution is reported as git reported it: a commit whose branch could not be resolved is not filed under one, and a detached HEAD leaves no branch marked as checked out."
              emptyReason="No branch was recorded for this repository. A repository with no commits has no branches yet, and one on a detached HEAD still has them."
            />
          </section>

          <LanguageStatistics language={record.primary_language} />
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setConfirmOpen(false)
        }}
        title="Remove this repository?"
        description={
          `“${record.name}” and every commit, branch and scan record NEXUS read from it will be ` +
          'removed. The activity event stays in your trail. This cannot be undone.'
        }
        confirmLabel="Remove repository"
        destructive
        pending={remove.isPending}
        onConfirm={() => {
          setConfirmOpen(false)
          onDelete()
        }}
      />
    </div>
  )
}

/* ------------------------------------------------------------------- panels */

/** A labelled figure. A `null` is an absence of measurement, and reads as one. */
function Fact({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium tabular-nums text-foreground">
        {value === null ? '—' : formatNumber(value)}
      </dd>
    </div>
  )
}

/**
 * What this repository is, in the words the scan recorded.
 *
 * Every null gets its own sentence rather than a fallback: `current_branch` is
 * null on a detached HEAD *and* on a repository with no commits, and those are
 * different states, so `describeCurrentBranch` distinguishes them by asking the
 * commit count. `latest_commit_at` is null for an empty repository and says so
 * instead of printing a date.
 */
function RepositoryFacts({
  repository,
  isStale,
}: {
  repository: RepositoryRead
  isStale: boolean
}) {
  return (
    <Card className="min-w-0">
      <CardHeader className="pb-3">
        <CardTitle>Repository</CardTitle>
        <CardDescription>
          Read from the local work tree at its last scan. Nothing here is re-read on its own.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="space-y-3">
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">Path</dt>
            <dd className="min-w-0 break-all font-mono text-xs text-foreground">
              {repository.local_path}
            </dd>
          </div>

          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">Branch checked out</dt>
            <dd className="text-sm font-medium text-foreground">
              {describeCurrentBranch(repository)}
            </dd>
            <dd className="text-xs text-muted-foreground">
              {repository.default_branch
                ? `Git resolved ${repository.default_branch} as the default branch.`
                : 'Git resolved no default branch — there is nothing for one to point at yet.'}
            </dd>
          </div>

          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">First commit</dt>
            <dd className="text-sm text-foreground">
              {repository.first_commit_at ? (
                <time dateTime={repository.first_commit_at} title={formatDeveloperInstant(repository.first_commit_at)}>
                  {formatDeveloperInstant(repository.first_commit_at)}
                </time>
              ) : (
                <span className="text-muted-foreground">
                  No commits yet, so there is no first commit to date.
                </span>
              )}
            </dd>
          </div>

          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">Latest commit</dt>
            <dd className="text-sm text-foreground">
              {repository.latest_commit_at ? (
                <time
                  dateTime={repository.latest_commit_at}
                  title={formatDeveloperInstant(repository.latest_commit_at)}
                >
                  {formatScanAge(repository.latest_commit_at)}
                </time>
              ) : (
                <span className="text-muted-foreground">No commits recorded yet.</span>
              )}
            </dd>
          </div>

          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">Branches recorded</dt>
            <dd className="text-sm font-medium tabular-nums text-foreground">
              {formatNumber(repository.branch_count)}
            </dd>
          </div>

          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">Working tree at scan time</dt>
            <dd className="text-sm text-foreground">
              {repository.working_tree_dirty
                ? 'Uncommitted changes were present when the scan ran.'
                : 'No uncommitted changes were present when the scan ran.'}
            </dd>
          </div>
        </dl>

        {isStale && (
          <p className="text-xs leading-relaxed text-warning">
            The last scan is more than {formatNumber(DEFAULT_STALE_HOURS)} hours old. Nothing here
            re-reads a repository on its own, so these figures are as of that scan rather than as of
            now — run a scan to bring them forward.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Where this repository's trail sits in the work record.
 *
 * `project_id` is null routinely: the recorded history outlives the project it was
 * attached to, because the foreign key is `ON DELETE SET NULL`. That is a
 * statement about linkage and not about the repository's value, so it reads as
 * an option rather than as a gap.
 */
function ProjectConnection({
  projectId,
  projectName,
  isLoading,
  isError,
}: {
  projectId: UUIDString | null
  projectName: string | null
  isLoading: boolean
  isError: boolean
}) {
  return (
    <Card className="min-w-0">
      <CardHeader className="pb-3">
        <CardTitle>Project</CardTitle>
        <CardDescription>
          Linking a repository to a project puts its recorded commits beside the work they served.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {projectId === null ? (
          <p className="text-sm leading-relaxed text-muted-foreground">
            Not linked to a project. The recorded history outlives the project it was attached to, so
            this is optional and can be set later.
          </p>
        ) : isLoading ? (
          <Skeleton className="h-5 w-40" />
        ) : isError || !projectName ? (
          <div className="space-y-2">
            <p className="text-sm leading-relaxed text-muted-foreground">
              The linked project could not be read. The repository itself is unaffected.
            </p>
            <Button asChild variant="outline" size="sm">
              <Link to={`/projects/${projectId}`}>Open the project</Link>
            </Button>
          </div>
        ) : (
          <Link
            to={`/projects/${projectId}`}
            className="inline-flex min-w-0 items-center gap-1.5 rounded-sm text-sm font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <FolderGit2 aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{projectName}</span>
          </Link>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * The language the scan recognised.
 *
 * **Only the most common tracked extension is kept**, so this is a fact about one
 * language rather than a distribution, and a chart of one bar would dress it up as
 * something it is not. A repository whose tracked files use extensions outside the
 * recognised set has no primary language at all — which is a real state and gets
 * its own sentence rather than an "Other" bucket the scan never produced.
 */
function LanguageStatistics({ language }: { language: string | null }) {
  return (
    <Card className="min-w-0">
      <CardHeader className="pb-3">
        <CardTitle>Language statistics</CardTitle>
        <CardDescription>
          Counted from the file extensions git tracks. There is no “other” bucket, because a bucket
          of unrecognised extensions is a category rather than a language.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {language ? (
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <Languages aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="text-muted-foreground">Most common tracked language</span>
            <span className="font-medium text-foreground">{language}</span>
          </p>
        ) : (
          <p className="text-sm leading-relaxed text-muted-foreground">
            No tracked language was recognised. Either the repository tracks no files, or every file
            it tracks uses an extension outside the recognised set — which is reported as nothing
            rather than as a category of its own.
          </p>
        )}
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
          The per-language file counts are read at scan time and summarised here as the single most
          common one; the full distribution is not retained per repository.
        </p>
      </CardContent>
    </Card>
  )
}

function CommitHistory({
  commits,
  total,
  isLoading,
  isStale,
  error,
  onRetry,
}: {
  commits: CommitRead[]
  total: number
  isLoading: boolean
  isStale: boolean
  error: unknown
  onRetry: () => void
}) {
  return (
    <section className="min-w-0 space-y-3" aria-labelledby="developer-commits">
      <h2 id="developer-commits" className="sr-only">
        Commit history
      </h2>
      {error && commits.length === 0 ? (
        <ErrorState
          error={toApiError(error)}
          title="The commit history could not load"
          onRetry={onRetry}
        />
      ) : (
        <CommitTimeline
          commits={commits}
          isLoading={isLoading}
          isStale={isStale}
          titleLevel="h3"
          title="Commit history"
          subtitle="Every commit the scan recorded in this repository, newest first. Each row is the record itself — no grouping, no streaks, and no figure for how long anything took."
          emptyReason="No commit has been recorded for this repository yet. Its commits appear once a scan reads the directory with the git CLI."
        />
      )}
      {/* Only once a real page of commits is on screen: during the read `shown` is
          0, and the scope note would claim a completeness it cannot know. */}
      {!isLoading && !error && <TimelineScopeNote total={total} shown={commits.length} />}
    </section>
  )
}

function RepositoryDetailSkeleton() {
  return (
    <div className="app-container space-y-6 py-6" aria-busy="true">
      <div className="space-y-3 border-b border-border pb-6">
        <Skeleton className="h-6 w-64 max-w-full" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {Array.from({ length: 3 }, (_, index) => (
          <Card key={index} className="min-w-0" aria-hidden="true">
            <CardContent className="space-y-3 p-6">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-3 w-32" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  )
}