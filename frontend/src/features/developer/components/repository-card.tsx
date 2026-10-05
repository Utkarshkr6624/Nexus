import type { MouseEvent, ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  CircleCheck,
  Clock,
  FolderGit2,
  GitBranch,
  GitCommitVertical,
  FolderOpen,
} from 'lucide-react'

import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { LanguageBadge, ScanStatusBadge } from '@/features/developer/components/developer-badges'
import { DeveloperEmptyState, DeveloperStaleNotice } from '@/features/developer/components/developer-empty-state'
import {
  DEFAULT_STALE_HOURS,
  describeCurrentBranch,
  formatScanAge,
  isScanStale,
  repositoryDirectoryName,
} from '@/features/developer/components/developer-format'
import {
  ACTIVITY_LEVEL_PIPS,
  NOT_ENOUGH_DATA_TITLE,
  activityLevelFor,
} from '@/features/developer/components/developer-vocabulary'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { RepositoryRead, UUIDString } from '@/types/developer'

/**
 * One registered repository, and the state of its last scan.
 *
 * ## What this card is allowed to say
 *
 * Everything on it describes **what git recorded**, never what a person did with
 * their time. There is no activity score, no "last active" line and no effort
 * figure, because the only thing the backend has is a commit timestamp — and a
 * timestamp records *that* a commit exists, not how long anyone was at it. The
 * Activity section therefore states a **count** and names the window it covers,
 * and `activityLevelFor` buckets that count into named levels rather than into a
 * score.
 *
 * ## The three nulls that decide this card's shape
 *
 * `RepositoryRead` is honest about what it does not know, and each null gets its
 * own sentence rather than a fallback value:
 *
 * - `current_branch: null` on a repository with commits means a **detached
 *   HEAD** — a normal state, not an error — while the same null on a repository
 *   with no commits means there is not a branch yet. `describeCurrentBranch`
 *   distinguishes them by asking the commit count, because collapsing the two
 *   would tell a reader their repository is broken when it is checked out at a
 *   commit.
 * - `latest_commit_at: null` means the repository has no commits, and the card
 *   prints the count it *does* have instead of inventing a date.
 * - `project_id: null` is normal — the trail outlives the project — and reads
 *   "Not linked to a project", which is a statement about linkage and not about
 *   the repository's value.
 *
 * ## The recent-commit count is supplied, not derived
 *
 * `RepositoryRead` carries whole-history `commit_count` and nothing about a
 * window, so the Activity section takes the window count as a prop and has three
 * honest states for it: a number (rendered with its window), `null` (explicitly
 * not measurable — the dashboard's "Not enough data yet."), and `undefined`
 * (the caller has no window figure for this repository at all, which is a
 * different thing and says so). None of the three is quietly turned into `0`.
 *
 * ## The actions slot is a footer, and why it cannot be a link
 *
 * A caller that can act on a repository — scan it, remove it — passes `actions`
 * and gets a footer strip below the figures. **It is a footer and not part of the
 * header because the card's only link is its title.** That is what keeps a
 * `<button>` from ever becoming a descendant of an `<a>`: buttons inside a link
 * are invalid HTML, and the browser resolves the conflict by navigating, so the
 * control a reader pressed does nothing but change the URL. Putting the slot
 * after `CardContent` makes that impossible by construction rather than by
 * discipline, and {@link stopActivation} is the cheap second half of the
 * guarantee — see its own comment.
 *
 * The slot is **absent when no caller supplies one**, so the read-only rendering
 * every other caller gets is byte-for-byte what it was before the slot existed.
 */

export interface RepositoryCardProps {
  repository: RepositoryRead
  /** Where the repository's own page lives. Omitted renders a read-only card. */
  href?: string | null
  /** Resolved name of the linked project, when the caller already has it. */
  projectName?: string | null
  projectHref?: string | null
  /**
   * Commits recorded for this repository inside the current window.
   * `number` renders, `null` is "not measurable", `undefined` is "not supplied".
   */
  recentCommitCount?: number | null
  /** The window `recentCommitCount` covers, so the sentence can be true. */
  recentWindowDays?: number | null
  /** How old a scan has to be before the card flags it. */
  staleAfterHours?: number
  /**
   * Controls for this repository, drawn in a footer below the figures.
   *
   * Omitted — the default — renders **no footer at all**, so a caller that only
   * reads gets exactly the card it got before this slot existed. A supplied node
   * is rendered as given: the card does not decide what the controls do, and it
   * never wraps them in the title's link.
   */
  actions?: ReactNode
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The decorative pip meter beside an activity level.
 *
 * `aria-hidden` and no text of its own: four filled squares read as a rating,
 * which is the one thing this surface must not do, so the sentence immediately
 * after it carries the meaning and the pips are only there to make the levels
 * orderable at a glance. The number of filled pips equals `level.pips`, so a
 * reader who cannot see them loses nothing the sentence did not already say.
 */
function ActivityPips({ filled }: { filled: number }) {
  return (
    <span aria-hidden="true" className="flex items-center gap-0.5">
      {Array.from({ length: ACTIVITY_LEVEL_PIPS }, (_, index) => (
        <span
          key={index}
          className={cn(
            'size-1.5 rounded-[2px]',
            index < filled ? 'bg-chart-1' : 'bg-muted',
          )}
        />
      ))}
    </span>
  )
}

/**
 * Keeps a press on an action from reaching whatever the card sits inside.
 *
 * **The structural guarantee is the real one:** the footer is rendered after
 * `CardContent` and the card's link wraps its title only, so no control here is a
 * descendant of an `<a>` and a `<button>` inside a link — invalid HTML that the
 * browser settles by navigating — cannot be produced by this component at all.
 *
 * This is what makes that survive the refactor nobody plans for. "Make the whole
 * card clickable" is a one-line change that wraps the card in its link, and on
 * that day an action has to go on acting rather than handing its click to the
 * router. Stopping the event here costs nothing today, because the card has no
 * click handler to stop, and keeps the invariant when it has one.
 */
function stopActivation(event: MouseEvent) {
  event.stopPropagation()
}

export function RepositoryCard({
  repository,
  href = null,
  projectName = null,
  projectHref = null,
  recentCommitCount,
  recentWindowDays = null,
  staleAfterHours = DEFAULT_STALE_HOURS,
  actions,
  titleLevel = 'h3',
  className,
}: RepositoryCardProps) {
  const stale = isScanStale(repository.last_scanned_at, staleAfterHours)
  const hasWindowCount = recentCommitCount !== undefined
  const level = hasWindowCount ? activityLevelFor(recentCommitCount ?? 0) : null
  const windowDays = recentWindowDays

  const title = (
    <CardTitle level={titleLevel} className="truncate text-base leading-snug">
      {repository.name}
    </CardTitle>
  )

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1.5">
            {href ? (
              <Link
                to={href}
                className={cn(
                  'block min-w-0 rounded-sm font-semibold leading-snug tracking-tight',
                  'hover:underline focus-visible:outline-none focus-visible:ring-2',
                  'focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                )}
              >
                {title}
              </Link>
            ) : (
              title
            )}

            <p
              className="flex items-center gap-1.5 truncate text-xs text-muted-foreground"
              title={repository.local_path}
            >
              <FolderOpen aria-hidden="true" className="size-3 shrink-0" />
              <span className="truncate">{repositoryDirectoryName(repository.local_path)}</span>
            </p>
          </div>

          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <ScanStatusBadge status={repository.last_scan_status} size="sm" />
            <LanguageBadge language={repository.primary_language} size="sm" />
          </div>
        </div>

        {repository.description && (
          <p className="line-clamp-2 text-sm leading-relaxed text-muted-foreground">
            {repository.description}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-1.5">
          {repository.is_active ? (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <CircleCheck aria-hidden="true" className="size-3" />
              Active
            </span>
          ) : (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Clock aria-hidden="true" className="size-3" />
              Marked inactive
            </span>
          )}
          {repository.working_tree_dirty && (
            <span className="text-xs text-muted-foreground">
              Uncommitted changes in the working tree at scan time
            </span>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <section className="space-y-1.5" aria-labelledby={`repo-${repository.id}-branch`}>
          <h4
            id={`repo-${repository.id}-branch`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Branch
          </h4>
          <p className="flex min-w-0 items-center gap-1.5 text-sm">
            <GitBranch aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate font-medium text-foreground">
              {describeCurrentBranch(repository)}
            </span>
          </p>
          <p className="text-xs text-muted-foreground">
            {formatNumber(repository.branch_count)}{' '}
            {repository.branch_count === 1 ? 'branch' : 'branches'} recorded at the last scan
            {repository.default_branch
              ? `, default branch ${repository.default_branch}`
              : ', and git resolved no default branch'}
            .
          </p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`repo-${repository.id}-history`}>
          <h4
            id={`repo-${repository.id}-history`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Recorded history
          </h4>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
            <div className="min-w-0">
              <dt className="text-xs text-muted-foreground">Commits</dt>
              <dd className="text-sm font-medium tabular-nums text-foreground">
                {formatNumber(repository.commit_count)}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-muted-foreground">Latest commit</dt>
              <dd className="text-sm font-medium text-foreground">
                {repository.latest_commit_at ? (
                  <time dateTime={repository.latest_commit_at}>
                    {formatScanAge(repository.latest_commit_at)}
                  </time>
                ) : (
                  <span className="text-muted-foreground">No commits yet</span>
                )}
              </dd>
            </div>
          </dl>
        </section>

        <section className="space-y-1.5" aria-labelledby={`repo-${repository.id}-activity`}>
          <h4
            id={`repo-${repository.id}-activity`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Activity
          </h4>
          {level ? (
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
              <ActivityPips filled={level.pips} />
              <span className="font-medium text-foreground">{level.label}</span>
              {typeof recentCommitCount === 'number' && (
                <span className="text-muted-foreground">
                  {formatNumber(recentCommitCount)}{' '}
                  {recentCommitCount === 1 ? 'commit' : 'commits'} recorded
                  {windowDays !== null ? ` in the last ${formatNumber(windowDays)} days` : ''}.
                </span>
              )}
            </p>
          ) : recentCommitCount === null ? (
            <p className="text-sm leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground">{NOT_ENOUGH_DATA_TITLE}</span> No
              window count has been measured for this repository, so no level is shown. The
              account metrics carry the window figures.
            </p>
          ) : (
            <p className="text-sm leading-relaxed text-muted-foreground">
              No per-repository window count was read for this card. Whole-history commits are
              above; the window figures live on the activity chart and the metric list.
            </p>
          )}
          {level && <p className="text-xs text-muted-foreground">{level.description}</p>}
        </section>

        <section className="space-y-1.5" aria-labelledby={`repo-${repository.id}-scan`}>
          <h4
            id={`repo-${repository.id}-scan`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Last scan
          </h4>
          <p className="flex flex-wrap items-center gap-1.5 text-sm">
            {repository.last_scan_status === 'error' ? (
              <GitCommitVertical aria-hidden="true" className="size-3.5 shrink-0 text-destructive" />
            ) : (
              <Clock aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            )}
            <span className="font-medium text-foreground">
              {repository.last_scanned_at
                ? `Scanned ${formatScanAge(repository.last_scanned_at)}`
                : 'Never scanned'}
            </span>
          </p>

          {stale && (
            <p className="text-xs leading-relaxed text-warning">
              Older than {formatNumber(staleAfterHours)} hours. Nothing re-reads a repository
              on its own — the figures above are as of that scan, not as of now.
            </p>
          )}

          {repository.last_scan_error && (
            <p className="text-xs leading-relaxed text-destructive">{repository.last_scan_error}</p>
          )}
        </section>

        <section className="space-y-1.5" aria-labelledby={`repo-${repository.id}-project`}>
          <h4
            id={`repo-${repository.id}-project`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Project
          </h4>
          {projectName ? (
            projectHref ? (
              <Link
                to={projectHref}
                className="inline-flex min-w-0 items-center gap-1.5 truncate text-sm font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <FolderGit2 aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate">{projectName}</span>
              </Link>
            ) : (
              <p className="flex min-w-0 items-center gap-1.5 text-sm">
                <FolderGit2 aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 truncate font-medium text-foreground">{projectName}</span>
              </p>
            )
          ) : (
            <p className="text-sm leading-relaxed text-muted-foreground">
              Not linked to a project. The recorded history outlives the project it was
              attached to, so linking one is optional.
            </p>
          )}
        </section>
      </CardContent>

      {actions && (
        <CardFooter
          onClick={stopActivation}
          className="flex-wrap items-center gap-2 border-t border-border py-3"
        >
          {actions}
        </CardFooter>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The repository card's silhouette.
 *
 * **It reserves the blocks the real card occupies** — header, two badges, three
 * labelled sections and a scan line — so the grid does not reflow when the data
 * lands. **Nothing here reads as a value**: the skeletons are grey blocks with no
 * digits and no pips, because a pulse in the shape of "12 commits" is a number
 * to anyone glancing at it, and on this surface an absent figure is never a
 * zero.
 */
export function RepositoryCardSkeleton({ className }: { className?: string }) {
  return (
    <Card className={cn('min-w-0', className)} aria-hidden="true">
      <CardHeader className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <Skeleton className="h-4 w-20 rounded-md" />
            <Skeleton className="h-4 w-24 rounded-md" />
          </div>
        </div>
        <Skeleton className="h-3 w-4/5" />
      </CardHeader>

      <CardContent className="space-y-4">
        {[0, 1, 2, 3].map((section) => (
          <div key={section} className="space-y-2">
            <Skeleton className="h-2.5 w-20" />
            <Skeleton className="h-3.5 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

export interface RepositoryCardGridSkeletonProps {
  /** How many card silhouettes to draw. */
  count?: number
  className?: string
}

/** The grid while its first read is in flight: the real cards' outlines, empty. */
export function RepositoryCardGridSkeleton({
  count = 6,
  className,
}: RepositoryCardGridSkeletonProps) {
  return (
    <div
      role="status"
      aria-busy="true"
      className={cn('grid gap-4 sm:grid-cols-2 xl:grid-cols-3', className)}
    >
      <span className="sr-only">Loading repositories</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <RepositoryCardSkeleton key={index} />
      ))}
    </div>
  )
}

/* ---------------------------------------------------------------------- grid */

export interface RepositoryCardGridProps {
  repositories: readonly RepositoryRead[]
  isLoading?: boolean
  /** A refetch is in flight behind rows already on screen. */
  isStale?: boolean
  /** Replaces the empty copy — the backend's own reason, verbatim. */
  emptyReason?: string | null
  /** Usually the page's "Register repository" button. */
  emptyAction?: ReactNode
  /** Builds each card's link. Omit for a read-only grid. */
  buildHref?: (repository: RepositoryRead) => string | null
  /**
   * Builds each card's controls. Omit — the default — and every card in the grid
   * renders with no footer at all, which is the read-only grid's whole contract.
   */
  buildActions?: (repository: RepositoryRead) => ReactNode
  projectNames?: Readonly<Record<UUIDString, string>>
  projectHrefs?: Readonly<Record<UUIDString, string>>
  /** Window commit count per repository; a missing key means "not supplied". */
  recentCommitCounts?: Readonly<Record<UUIDString, number | null>>
  recentWindowDays?: number | null
  staleAfterHours?: number
  /** Silhouettes to draw while loading. */
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The repository grid, with its loading, empty and stale states.
 *
 * **The grid is the loading state.** Returning `null` while the first read is in
 * flight would collapse the page to its header and then push everything back
 * down as the cards arrived; drawing the real grid's silhouettes keeps the
 * column count visible so the layout does not move when the rows land.
 *
 * `min-w-0` on the grid *and* on every card in it: a repository name or a
 * resolved path is the one string on this surface with no length limit, and
 * without it a single long name pushes its grid column — and on a phone the page
 * itself — into a horizontal scroll.
 *
 * The counts maps are keyed by `repository_id` and read with `?.` rather than
 * `??`, so a repository the caller has no window figure for takes the "not
 * supplied" branch of its card instead of being drawn as zero commits.
 *
 * `buildActions` is passed down **whole**, rather than being called once and
 * spread: a card's controls hold their own mutation state, and a factory the
 * grid invokes once would give every card on the page the same "scan running"
 * flag — so one repository's scan would grey out every other repository's button.
 */
export function RepositoryCardGrid({
  repositories,
  isLoading = false,
  isStale = false,
  emptyReason = null,
  emptyAction,
  buildHref,
  buildActions,
  projectNames,
  projectHrefs,
  recentCommitCounts,
  recentWindowDays = null,
  staleAfterHours = DEFAULT_STALE_HOURS,
  skeletonCount = 6,
  titleLevel = 'h3',
  className,
}: RepositoryCardGridProps) {
  if (isLoading) {
    return <RepositoryCardGridSkeleton count={skeletonCount} className={className} />
  }

  if (repositories.length === 0) {
    return (
      <DeveloperEmptyState
        variant="repositories"
        reason={emptyReason}
        action={emptyAction}
        className={cn('rounded-lg border border-border bg-card', 'min-h-[16rem]', className)}
      />
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      <DeveloperStaleNotice isStale={isStale} subject="the repository list" />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {repositories.map((repository) => {
          const recent = recentCommitCounts?.[repository.id]
          return (
            <RepositoryCard
              key={repository.id}
              repository={repository}
              href={buildHref ? buildHref(repository) : null}
              actions={buildActions ? buildActions(repository) : undefined}
              projectName={projectNames?.[repository.id] ?? null}
              projectHref={projectHrefs?.[repository.id] ?? null}
              recentCommitCount={recent}
              recentWindowDays={recentWindowDays}
              staleAfterHours={staleAfterHours}
              titleLevel={titleLevel}
            />
          )
        })}
      </div>
    </div>
  )
}

