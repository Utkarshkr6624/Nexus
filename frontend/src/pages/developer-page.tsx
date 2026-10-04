import { useCallback, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Code2, FolderPlus, Plus, RefreshCw } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LiveStatus } from '@/components/feedback/live-status'
import { PageHeader } from '@/components/feedback/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import {
  CommitTimeline,
  DeveloperActivitySection,
  DeveloperEmptyState,
  DeveloperMetricList,
  DeveloperSummaryTiles,
  LanguageBreakdown,
  RepositoryCardGrid,
  TimelineScopeNote,
  languageCountsFromRepositories,
} from '@/features/developer/components'
import {
  DEVELOPER_WINDOW_PRESETS,
  useCreateRepository,
  useDeveloperActivity,
  useDeveloperCommits,
  useDeveloperMetrics,
  useDeveloperSummary,
  useDeveloperWindow,
  useRepositories,
} from '@/features/developer/hooks'
import type { DeveloperWindow, DeveloperWindowPresetId } from '@/features/developer/hooks'
import { useProjects } from '@/features/work/hooks'
import type { ApiError } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { toApiError, bannerError, fieldErrorMessages } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { isDateOnly, rangeDays } from '@/types/analytics'
import {
  MAX_DEVELOPER_WINDOW_DAYS,
  type CommitRead,
  type DeveloperActivityRead,
  type RepositoryCreatePayload,
  type RepositoryRead,
  type UUIDString,
} from '@/types/developer'

/**
 * The Developer Intelligence dashboard.
 *
 * ## What this page is allowed to say
 *
 * Everything it renders is a **count of something a repository recorded**: commits,
 * distinct days carrying a commit, branches, lines added and removed. There is no
 * tile for hours, focus, effort or productivity anywhere on it, and the reason is
 * not editorial caution — a git timestamp records that a commit exists at an
 * instant and nothing about how long anyone was at it, so a "6 hours focused"
 * figure could not be computed here even if a page wanted to print it. The
 * metric tiles below the fold repeat the same discipline at a finer grain, and
 * each one carries the sentence explaining what it is built from.
 *
 * ## The page composes the feature's components; it does not draw rows
 *
 * `features/developer/components` owns every presentational decision — the tile
 * row, the chart and its grain control, the repository grid, the commit timeline,
 * the eight explained metrics and the empty states. What is left to this file is
 * what only a page can decide: which reads are in flight, which filters are in
 * force, where those filters live, and which sentence describes the window the
 * server actually answered for.
 *
 * **Everything view-shaped is in the URL.** `?range=`, `?granularity=`,
 * `?status=`, `?project=` and `?page=` make the dashboard shareable, survive a
 * reload, and let the back button step out of a filter rather than out of the
 * page. The window is resolved by `useDeveloperWindow`, which sends *no*
 * `window_days` at all for the default preset so the backend applies its own
 * configured default — the caption below the presets quotes the window the
 * server echoed back rather than the one the client asked for, because those can
 * differ and only the server knows.
 *
 * ## Placeholder data is disclosed, never silently presented
 *
 * Every window-shaped read carries `placeholderData: (previous) => previous`, so
 * switching range keeps the previous window on screen instead of blanking the
 * page. The figures behind it are real but they are the *previous* answer, so
 * each section is told `isStale` and renders the refreshing line rather than
 * passing stale numbers off as current.
 *
 * ## A failed scan is not an error here
 *
 * `POST .../scan` answers 200 with `status: 'error'` and a human sentence whether
 * or not git could read the directory, so the dashboard never has to survive an
 * exception from a broken repository: the sentence is rendered by the repository
 * card and, after a manual scan, by the scan record on the detail page. The
 * dashboard itself only registers and rescans — the last of which is reachable
 * from a repository's own page.
 */
const REPOSITORY_PAGE_LIMIT = 12
const TIMELINE_LIMIT = 25
const PROJECT_LIMIT = 100

/** Stable empty array, so a memo below is not re-created on every render. */
const NO_REPOSITORIES: RepositoryRead[] = []
const NO_COMMITS: CommitRead[] = []

/**
 * `?status=` values.
 *
 * An *absent* parameter already means "every repository", so "All" needs a word
 * of its own — otherwise the control would show "All repositories" selected
 * beside a list the server had been told to filter.
 */
const ALL_STATUSES = 'all'

const STATUS_OPTIONS = [
  { value: ALL_STATUSES, label: 'All repositories' },
  { value: 'active', label: 'Active only' },
  { value: 'inactive', label: 'Inactive only' },
] as const

/**
 * Reads a URL parameter back into the boolean the API takes.
 *
 * A hand-edited link must not put an unknown word on the wire: `?status=nonsense`
 * would be a 422, so an unrecognised value is dropped and the page renders its
 * unfiltered view instead of an error the reader cannot act on.
 */
function isActiveFromParam(value: string | null): boolean | undefined {
  if (value === 'active') return true
  if (value === 'inactive') return false
  return undefined
}

function pageFromParam(value: string | null): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1
}

export default function DeveloperPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const window = useDeveloperWindow()
  const [formOpen, setFormOpen] = useState(false)

  const statusParam = searchParams.get('status')
  const status = statusParam === ALL_STATUSES ? undefined : (statusParam ?? ALL_STATUSES)
  const isActive = isActiveFromParam(status ?? null)
  const projectId = searchParams.get('project') ?? undefined
  const page = pageFromParam(searchParams.get('page'))

  /**
   * Window-shaped reads.
   *
   * The summary and the metrics take `window_days` alone; the activity series
   * also takes the grain, because day/week/month re-bucket the same commits
   * rather than filtering them. Both are memoised by the window hook, so a
   * re-render does not rebuild the params object and thrash the query key.
   */
  const summaryParams = useMemo(() => ({ window_days: window.window_days }), [window.window_days])
  const summary = useDeveloperSummary(summaryParams)
  const metrics = useDeveloperMetrics(summaryParams)
  const activity = useDeveloperActivity(window.params)
  const commits = useDeveloperCommits({ limit: TIMELINE_LIMIT, offset: 0 })
  const repositories = useRepositories({
    limit: REPOSITORY_PAGE_LIMIT,
    offset: (page - 1) * REPOSITORY_PAGE_LIMIT,
    is_active: isActive,
    project_id: projectId,
  })
  const projects = useProjects({ limit: PROJECT_LIMIT, offset: 0 })

  const apply = useCallback(
    (patch: Record<string, string | undefined>, resetPage = true) => {
      const next = new URLSearchParams(searchParams)
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined) next.delete(key)
        else next.set(key, value)
      }
      // A filter change invalidates the page number: leaving `page=3` behind
      // after narrowing to two rows would land the reader on an empty third page
      // and call it an absence.
      if (resetPage) next.delete('page')
      setSearchParams(next)
    },
    [searchParams, setSearchParams],
  )

  const rows = repositories.data?.items ?? NO_REPOSITORIES
  const total = repositories.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / REPOSITORY_PAGE_LIMIT))
  const filtering = isActive !== undefined || projectId !== undefined

  /**
   * The commit timeline's repository names.
   *
   * Resolved from the repositories **currently on screen**, so a row whose
   * repository is on another page of the grid renders without a name rather than
   * with a name this page has not read. The timeline says how many commits it is
   * showing either way.
   */
  const namesById = useMemo(() => {
    const map: Record<UUIDString, string> = {}
    for (const repository of rows) map[repository.id] = repository.name
    return map
  }, [rows])

  const projectNames = useMemo(() => {
    const map: Record<UUIDString, string> = {}
    for (const project of projects.data?.items ?? []) map[project.id] = project.name
    return map
  }, [projects.data])

  const projectHrefs = useMemo(() => {
    const map: Record<UUIDString, string> = {}
    for (const id of Object.keys(projectNames)) map[id] = `/projects/${id}`
    return map
  }, [projectNames])

  const languages = useMemo(() => languageCountsFromRepositories(rows), [rows])

  /**
   * The cold-start branch: nothing is registered at all, so there is no trail to
   * put on screen and the page explains that instead of printing zeroes.
   *
   * Keyed on the **repository list** rather than on `summary.has_data`, because
   * the two are different questions: an account can have registered work trees
   * that no scan has read yet, and `has_data` is false for that account too.
   * Branching on it would have shown "No repositories registered yet" beside a
   * list of registered repositories. A filter that hides everything is not a
   * cold start either, so it is excluded — that case is the grid's own
   * "nothing matches this filter" state.
   */
  const coldStart = repositories.data !== undefined && total === 0 && !filtering

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title="Developer Intelligence"
        eyebrow={
          <>
            <Code2 className="size-3.5" aria-hidden="true" />
            Local repositories
          </>
        }
        badges={
          summary.data ? (
            <span className="text-xs text-muted-foreground">
              {summary.data.has_data
                ? `Figures cover the last ${summary.data.window_days} days`
                : coldStart
                  ? 'Nothing registered yet'
                  : 'Registered, but no scan has read one yet'}
            </span>
          ) : null
        }
        actions={
          <Button type="button" onClick={() => setFormOpen(true)}>
            <Plus aria-hidden="true" />
            Register repository
          </Button>
        }
        description="Everything below is read from local git history with the git CLI, on the machine NEXUS runs on. It reports the commits your repositories recorded — never how long you worked, how focused you were, or how productive the period was, because a commit timestamp cannot prove any of those."
      />

      <WindowBar window={window} />

      {coldStart ? (
        <DeveloperEmptyState
          variant="repositories"
          action={
            <Button type="button" onClick={() => setFormOpen(true)}>
              <Plus aria-hidden="true" />
              Register repository
            </Button>
          }
          className="rounded-lg border border-border bg-card"
        />
      ) : (
        <>
          <section className="space-y-3">
            <h2 className="sr-only">Overview</h2>
            {summary.isPending && !summary.data ? (
              <DeveloperSummaryTilesSkeleton />
            ) : summary.isError && !summary.isPlaceholderData ? (
              <ErrorState
                error={toApiError(summary.error)}
                title="The developer counts could not load"
                compact
                onRetry={() => void summary.refetch()}
              />
            ) : summary.data?.has_data ? (
              <DeveloperSummaryTiles
                summary={summary.data}
                isStale={summary.isPlaceholderData || (summary.isFetching && !summary.isPending)}
              />
            ) : (
              // Repositories exist, but no scan has read one yet. `has_data` is
              // false here too, and the tile row's own empty copy would claim no
              // repository is registered — so this state gets its own sentence
              // instead, and everything below the fold stays on screen where the
              // evidence will appear once a scan runs.
              <DeveloperEmptyState
                variant="scanRuns"
                className="rounded-lg border border-border bg-card"
              />
            )}
          </section>

          <div className="grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <ActivityPanel
                activity={activity.data}
                window={window}
                isLoading={activity.isPending}
                isStale={activity.isPlaceholderData || (activity.isFetching && !activity.isPending)}
                onRetry={() => void activity.refetch()}
                error={activity.isError && !activity.isPlaceholderData ? activity.error : null}
              />
            </div>
            <ChangeTotals
              activity={activity.data}
              isLoading={activity.isPending}
              granularity={window.granularity}
            />
          </div>

          <section aria-labelledby="developer-repositories" className="space-y-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <h2 id="developer-repositories" className="text-base font-semibold text-foreground">
                  Repositories
                </h2>
                <p className="text-xs text-muted-foreground">
                  One card per registered work tree. Open one to see its branches, its own commit
                  history and its scan record.
                </p>
              </div>
            </div>

            <div className="rounded-lg border border-border bg-card p-3">
              <h3 className="sr-only">Repository filters</h3>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <div className="app-form-field">
                  <Label htmlFor="developer-status">Repository state</Label>
                  <Select
                    id="developer-status"
                    value={status}
                    onChange={(event) => apply({ status: event.target.value })}
                  >
                    {STATUS_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </div>

                <div className="app-form-field">
                  <Label htmlFor="developer-project">Project</Label>
                  <Select
                    id="developer-project"
                    value={projectId ?? ''}
                    onChange={(event) => apply({ project: event.target.value || undefined })}
                  >
                    <option value="">All projects</option>
                    {(projects.data?.items ?? []).map((project) => (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>

              <LiveStatus
                active={repositories.isPlaceholderData}
                className="mt-3 text-xs text-muted-foreground"
              >
                Updating for the selected filters…
              </LiveStatus>
            </div>

            {repositories.isError && !repositories.isPlaceholderData ? (
              <ErrorState
                error={toApiError(repositories.error)}
                title="The repository list could not load"
                onRetry={() => void repositories.refetch()}
              />
            ) : (
              <RepositoryCardGrid
                repositories={rows}
                isLoading={repositories.isPending && !repositories.data}
                isStale={repositories.isPlaceholderData}
                projectNames={projectNames}
                projectHrefs={projectHrefs}
                recentWindowDays={summary.data?.window_days ?? null}
                skeletonCount={6}
                buildHref={(repository) => `/developer/${repository.id}`}
                emptyReason={
                  filtering
                    ? 'The repositories are registered and scanned; none of them match the filters selected above. Clearing the filters shows them again.'
                    : null
                }
                emptyAction={
                  <Button type="button" onClick={() => setFormOpen(true)}>
                    <FolderPlus aria-hidden="true" />
                    Register a repository
                  </Button>
                }
              />
            )}

            {total > REPOSITORY_PAGE_LIMIT && (
              <nav className="flex items-center justify-between gap-3" aria-label="Repository pages">
                <p className="text-xs text-muted-foreground">
                  Page {page} of {totalPages} · {total} repositor
                  {total === 1 ? 'y' : 'ies'}
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page <= 1}
                    onClick={() =>
                      apply({ page: page - 1 > 1 ? String(page - 1) : undefined }, false)
                    }
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= totalPages}
                    onClick={() =>
                      apply({ page: page + 1 > 1 ? String(page + 1) : undefined }, false)
                    }
                  >
                    Next
                  </Button>
                </div>
              </nav>
            )}
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <TimelinePanel
              commits={commits.data?.items ?? NO_COMMITS}
              total={commits.data?.total ?? 0}
              isLoading={commits.isPending && !commits.data}
              isStale={commits.isPlaceholderData}
              namesById={namesById}
              error={commits.isError && !commits.isPlaceholderData ? commits.error : null}
              onRetry={() => void commits.refetch()}
            />

            <LanguageBreakdown
              languages={languages}
              isStale={repositories.isPlaceholderData}
              subtitle="One bar per recognised primary language across the repositories on screen. A repository whose tracked files use unrecognised extensions contributes nothing — there is no “other” bucket, because that would be a category rather than a language."
            />
          </div>

          <section aria-labelledby="developer-metrics" className="space-y-3">
            <div className="min-w-0 space-y-1">
              <h2 id="developer-metrics" className="text-base font-semibold text-foreground">
                Metrics
              </h2>
              <p className="text-xs text-muted-foreground">
                Eight measures of recorded history. Each one states how it is computed and repeats
                itself with the figures it was built from. Where the data cannot support one, it
                says why instead of reporting zero.
              </p>
            </div>

            {metrics.isPending && !metrics.data ? (
              <DeveloperMetricList metrics={[]} isLoading />
            ) : metrics.isError && !metrics.isPlaceholderData ? (
              <ErrorState
                error={toApiError(metrics.error)}
                title="The metrics could not load"
                onRetry={() => void metrics.refetch()}
              />
            ) : metrics.data ? (
              <DeveloperMetricList
                metrics={metrics.data}
                titleLevel="h3"
                isStale={metrics.isPlaceholderData || (metrics.isFetching && !metrics.isPending)}
              />
            ) : null}
          </section>

          <p className="text-xs leading-relaxed text-muted-foreground">
            Reading this page: a commit count says how many commits git recorded in the window, and
            an <em>active day</em> is a calendar day that carried at least one of them. Neither is a
            measure of time spent — a repository can hold a thousand commits from one afternoon and a
            single commit from a fortnight of evenings, and NEXUS cannot tell the difference, so it
            does not claim to.
          </p>
        </>
      )}

      <RegisterRepositoryDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        projects={(projects.data?.items ?? []).map((project) => ({ id: project.id, name: project.name }))}
      />
    </div>
  )
}

/* ------------------------------------------------------------------- panels */

/**
 * The window picker: presets, a custom range and the sentence stating which
 * window the server answered for.
 *
 * **The caption quotes the response, not the request.** The default preset sends
 * no `window_days` at all so the backend applies its own
 * `developer_default_window_days`, so the length on screen can differ from the
 * 30 the presets would suggest. Printing the client's own guess would be a
 * caption the server cannot back.
 */
function WindowBar({ window }: { window: DeveloperWindow }) {
  return (
    <section className="space-y-3" aria-labelledby="developer-window">
      <h2 id="developer-window" className="sr-only">
        Window
      </h2>
      <div className="rounded-lg border border-border bg-card p-3">
        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1.5">
            <p className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
              Window
            </p>
            <PresetButtons window={window} />
          </div>

          <CustomRange window={window} />
        </div>

        <p className="mt-3 text-xs text-muted-foreground">
          A developer window is a trailing span of days, resolved backwards from today by the server.
          Everything on this page is computed over it, and the summaries and metrics say so in their
          own sentences.
        </p>
      </div>
    </section>
  )
}

function PresetButtons({ window }: { window: DeveloperWindow }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Window presets">
      {DEVELOPER_WINDOW_PRESETS.filter((preset) => preset.id !== 'custom').map((preset) => {
        const selected = window.preset === preset.id
        return (
          <Button
            key={preset.id}
            type="button"
            size="sm"
            variant={selected ? 'default' : 'outline'}
            aria-pressed={selected}
            onClick={() => window.setPreset(preset.id as DeveloperWindowPresetId)}
          >
            {preset.label}
          </Button>
        )
      })}
      <Button
        type="button"
        size="sm"
        variant={window.preset === 'custom' ? 'default' : 'outline'}
        aria-pressed={window.preset === 'custom'}
        onClick={() => window.setPreset('custom')}
      >
        Custom
      </Button>
    </div>
  )
}

/**
 * A custom range, applied rather than typed-on-change.
 *
 * The two inputs are uncontrolled and seeded from `window.custom`, which the
 * hook always resolves — so switching to Custom restores the range that was
 * chosen last instead of resetting it, and typing in a field does not fire four
 * requests before the range is coherent. An inverted range is refused here with
 * a sentence rather than sent as a 422, and an over-wide one is accepted *and
 * disclosed*, because the hook clamps it to what the API accepts and a silently
 * shortened window would be a lie about what is on screen.
 */
function CustomRange({ window }: { window: DeveloperWindow }) {
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const custom = window.custom

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setNotice(null)

    const form = new FormData(event.currentTarget)
    const start = String(form.get('start') ?? '')
    const end = String(form.get('end') ?? '')

    if (!isDateOnly(start) || !isDateOnly(end)) {
      setError('Enter both dates as YYYY-MM-DD.')
      return
    }
    if (start > end) {
      setError('The start date cannot be after the end date.')
      return
    }

    const days = rangeDays(start, end)
    window.setCustom(start, end)
    setNotice(
      days > MAX_DEVELOPER_WINDOW_DAYS
        ? `${days} days is wider than the API accepts, so the window was clamped to ${MAX_DEVELOPER_WINDOW_DAYS} days.`
        : null,
    )
  }

  return (
    <form onSubmit={submit} className="space-y-1.5">
      <p className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
        Custom range
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor="developer-window-start" className="sr-only">
          Window start date
        </Label>
        <Input
          id="developer-window-start"
          name="start"
          type="date"
          defaultValue={custom?.start ?? ''}
          className="w-40"
        />
        <Label htmlFor="developer-window-end" className="sr-only">
          Window end date
        </Label>
        <Input
          id="developer-window-end"
          name="end"
          type="date"
          defaultValue={custom?.end ?? ''}
          className="w-40"
        />
        <Button type="submit" size="sm" variant="outline">
          Apply range
        </Button>
      </div>
      {error && (
        <p role="alert" className="app-form-error">
          {error}
        </p>
      )}
      {notice && <p className="text-xs text-warning">{notice}</p>}
    </form>
  )
}

function ActivityPanel({
  activity,
  window,
  isLoading,
  isStale,
  error,
  onRetry,
}: {
  activity: DeveloperActivityRead | undefined
  window: DeveloperWindow
  isLoading: boolean
  isStale: boolean
  error: unknown
  onRetry: () => void
}) {
  if (error && !activity) {
    return (
      <ErrorState
        error={toApiError(error)}
        title="The activity series could not load"
        onRetry={onRetry}
      />
    )
  }

  return (
    <DeveloperActivitySection
      activity={activity ?? null}
      isLoading={isLoading}
      isStale={isStale}
      granularity={window.granularity}
      onGranularityChange={window.setGranularity}
      scopeLabel={null}
    />
  )
}

/**
 * The three change figures the summary does not carry separately: files
 * touched, lines added and lines removed inside the window.
 *
 * **Summed from the buckets the activity endpoint returned**, which are dense
 * and zero-filled, so the totals cover the whole window rather than the days that
 * happened to carry a commit. The caption says exactly what the sum means: a
 * file edited in three commits is counted three times, because git recorded three
 * changes to it and collapsing those into one would be a different measurement.
 *
 * While the series is loading the figures are skeletons, and a failed or absent
 * series renders dashes — never zeros, which would assert that nothing changed.
 */
function ChangeTotals({
  activity,
  isLoading,
  granularity,
}: {
  activity: DeveloperActivityRead | undefined
  isLoading: boolean
  granularity: DeveloperWindow['granularity']
}) {
  const totals = useMemo(() => {
    if (!activity) return null
    // `buckets` is required by the wire type, but a degraded or partially
    // cached response should blank one total rather than crash the dashboard
    // into the route error boundary. The zero-filled default keeps every
    // consumer on the same "no measurement" path it already handles.
    return (activity.buckets ?? []).reduce(
      (sum, bucket) => ({
        additions: sum.additions + bucket.additions,
        deletions: sum.deletions + bucket.deletions,
        files: sum.files + bucket.files_changed,
      }),
      { additions: 0, deletions: 0, files: 0 },
    )
  }, [activity])

  return (
    <Card className="min-w-0">
      <CardHeader className="pb-3">
        <CardTitle>Recent activity</CardTitle>
        <CardDescription>
          What the {(activity?.buckets ?? []).length} {granularity} buckets of this window recorded.
          Each commit's changed files are counted once per commit, so a file edited three times is
          counted three times.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && !totals ? (
          <div className="space-y-3" role="status" aria-busy="true">
            <span className="sr-only">Loading the change statistics</span>
            <Skeleton className="h-7 w-24" />
            <Skeleton className="h-7 w-24" />
            <Skeleton className="h-7 w-24" />
          </div>
        ) : (
          <dl className="space-y-3">
            <ChangeFigure label="Files changed" value={totals?.files ?? null} />
            <ChangeFigure label="Lines added" value={totals?.additions ?? null} />
            <ChangeFigure label="Lines removed" value={totals?.deletions ?? null} />
          </dl>
        )}
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          These are counts of lines git recorded in a diff. They say nothing about how the change was
          made or how long it took.
        </p>
      </CardContent>
    </Card>
  )
}

function ChangeFigure({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium tabular-nums text-foreground">
        {value === null ? '—' : value.toLocaleString()}
      </dd>
    </div>
  )
}

function TimelinePanel({
  commits,
  total,
  isLoading,
  isStale,
  namesById,
  error,
  onRetry,
}: {
  commits: CommitRead[]
  total: number
  isLoading: boolean
  isStale: boolean
  namesById: Record<UUIDString, string>
  error: unknown
  onRetry: () => void
}) {
  return (
    <section className="min-w-0 space-y-3" aria-labelledby="developer-timeline">
      <h2 id="developer-timeline" className="sr-only">
        Commit timeline
      </h2>
      {error && commits.length === 0 ? (
        <ErrorState error={toApiError(error)} title="The commit timeline could not load" onRetry={onRetry} />
      ) : (
        <CommitTimeline
          commits={commits}
          isLoading={isLoading}
          isStale={isStale}
          titleLevel="h3"
          title="Commit timeline"
          subtitle="Every commit a scan has recorded across every repository you own, newest first. Each row is the evidence itself: who committed, when, on which branch, and how many lines moved."
          emptyReason="No commit has been recorded yet. Commits appear when a scan reads a registered repository with the git CLI."
          repositoryName={(id) => namesById[id] ?? null}
          repositoryHref={(id) => (namesById[id] ? `/developer/${id}` : null)}
        />
      )}
      {/* Shown only once a real page of commits is on screen: while the read is
          in flight `shown` is 0, and "every commit is shown here" would be a
          claim about a list that has not arrived. */}
      {!isLoading && !error && <TimelineScopeNote total={total} shown={commits.length} />}
    </section>
  )
}

/**
 * The dashboard's own placeholder for the tile row.
 *
 * `DeveloperSummaryTiles` has no loading state of its own — a component that
 * reads counts cannot invent them — so the six silhouettes are reserved here at
 * the same grid. Nothing in them resembles a figure: a pulse in the shape of
 * “12 commits” is a count to anyone glancing at it.
 */
function DeveloperSummaryTilesSkeleton() {
  return (
    <div role="status" aria-busy="true" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
      <span className="sr-only">Loading the developer summary</span>
      {Array.from({ length: 6 }, (_, index) => (
        <Card key={index} className="min-w-0" aria-hidden="true">
          <CardContent className="space-y-3 p-6">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-7 w-16" />
            <Skeleton className="h-3 w-4/5" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

/* -------------------------------------------------------- registration form */

interface ProjectOption {
  id: UUIDString
  name: string
}

/**
 * Registers a local git work tree.
 *
 * **The path is validated by the backend, not here.** `POST /developer/repositories`
 * resolves the path, proves it is a git work tree and refuses anything else with
 * a 422 and a sentence — so this form never inspects the filesystem, cannot
 * promise a path is valid, and surfaces the server's own message verbatim. A
 * bare `git init` with no commits is a valid repository and registers fine.
 *
 * Name and description are optional: the backend defaults the name to the
 * directory name, and the project association can be added later, because the
 * recorded trail outlives the project it was attached to.
 */
function RegisterRepositoryDialog({
  open,
  onOpenChange,
  projects,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  projects: ProjectOption[]
}) {
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [projectId, setProjectId] = useState('')
  const [error, setError] = useState<ApiError | null>(null)

  const create = useCreateRepository()
  const pending = create.isPending

  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function reset() {
    setPath('')
    setName('')
    setDescription('')
    setProjectId('')
    setError(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    const localPath = path.trim()
    if (!localPath) {
      setError(toApiError(new Error('A repository needs the absolute path to its git work tree.')))
      return
    }

    const payload: RepositoryCreatePayload = {
      local_path: localPath,
      ...(name.trim() ? { name: name.trim() } : {}),
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(projectId ? { project_id: projectId } : {}),
    }

    try {
      const saved = await create.mutateAsync(payload)
      toast.success(
        'Repository registered',
        `${saved.name} is recorded. Run a scan on its page to read its commits.`,
      )
      reset()
      onOpenChange(false)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error('Could not register that repository', apiError.message)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (pending) return
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Register a repository</DialogTitle>
          <DialogDescription>
            NEXUS reads the path with the git CLI on the machine it runs on — there is no hosted
            service and no account to connect. The path is checked to be a git work tree before
            anything is stored, and registration does not open it: the first scan does that.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <form className="app-form-stack" onSubmit={submit} noValidate>
            {banner && (
              <p role="alert" className="app-form-error">
                {banner.message}
              </p>
            )}

            <div className="app-form-field">
              <Label htmlFor="repository-path">Local path</Label>
              <Input
                id="repository-path"
                value={path}
                required
                spellCheck={false}
                autoComplete="off"
                error={Boolean(fieldErrors.local_path)}
                placeholder="/home/you/code/my-project"
                onChange={(event) => {
                  setPath(event.target.value)
                  setError(null)
                }}
              />
              {fieldErrors.local_path ? (
                <p className="app-form-error">{fieldErrors.local_path}</p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  The absolute path to a directory containing a <code>.git</code> entry. Relative
                  paths are resolved before they are stored, so a path cannot later point somewhere
                  else.
                </p>
              )}
            </div>

            <div className="app-form-field">
              <Label htmlFor="repository-name" optional>
                Name
              </Label>
              <Input
                id="repository-name"
                value={name}
                maxLength={200}
                error={Boolean(fieldErrors.name)}
                placeholder="Defaults to the directory name"
                onChange={(event) => setName(event.target.value)}
              />
              {fieldErrors.name && <p className="app-form-error">{fieldErrors.name}</p>}
            </div>

            <div className="app-form-field">
              <Label htmlFor="repository-description" optional>
                Description
              </Label>
              <textarea
                id="repository-description"
                rows={3}
                maxLength={2000}
                value={description}
                aria-invalid={Boolean(fieldErrors.description) || undefined}
                placeholder="Optional"
                onChange={(event) => setDescription(event.target.value)}
                className={cn(
                  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm',
                  'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                  'aria-[invalid=true]:border-destructive',
                )}
              />
              {fieldErrors.description && (
                <p className="app-form-error">{fieldErrors.description}</p>
              )}
            </div>

            <div className="app-form-field">
              <Label htmlFor="repository-project" optional>
                Project
              </Label>
              <Select
                id="repository-project"
                value={projectId}
                aria-invalid={Boolean(fieldErrors.project_id) || undefined}
                onChange={(event) => setProjectId(event.target.value)}
              >
                <option value="">Not linked to a project</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </Select>
              {fieldErrors.project_id ? (
                <p className="app-form-error">{fieldErrors.project_id}</p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Optional. The recorded history outlives the project it was attached to, so this can
                  be set later or left unset.
                </p>
              )}
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                disabled={pending}
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={pending || !path.trim()}>
                {pending ? (
                  <>
                    <Spinner size="sm" />
                    Registering…
                  </>
                ) : (
                  <>
                    <RefreshCw aria-hidden="true" />
                    Register repository
                  </>
                )}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}

