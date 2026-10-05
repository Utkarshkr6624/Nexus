import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Compass, Radar, Search, ShieldCheck } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useCommandPaletteStore } from '@/features/command-palette/command-palette-store'
import { useOverview } from '@/features/analytics/hooks'
import { useDeveloperSummary } from '@/features/developer/hooks'
import { useLearningGoals, useLearningSummary } from '@/features/learning/hooks'
import { usePlannerConflicts } from '@/features/planner/hooks'
import { useRecommendations, useRiskSummary, useRisks } from '@/features/risk/hooks'
import { useActivityStats, useTasks } from '@/features/work/hooks'
import { usesCommandKey } from '@/hooks/use-command-palette'
import { useMlStatus } from '@/features/command-center/hooks'
import { MlInsightPanel } from '@/features/command-center/components/ml-insight-panel'
import { QuickActions } from '@/features/command-center/components/quick-actions'
import { PriorityQueue } from '@/features/command-center/components/priority-queue'
import { CommandCenterPanel } from '@/features/command-center/components/panel'
import type { PanelState } from '@/features/command-center/components/panel'
import {
  EngineeringPanelBody,
  FindingsPanelBody,
  LearningPanelBody,
  MomentumPanelBody,
} from '@/features/command-center/components/measured-panels'
import { collectSignals, dateOnlyOf, shiftDateOnly } from '@/features/command-center/priority'
import { toApiError } from '@/services/errors'
import type { Conflict } from '@/types/planner'
import type { LearningGoalRead } from '@/types/learning'
import type { RecommendationRead, RiskRead } from '@/types/risk'
import type { Task, TaskStats } from '@/types/work'

/**
 * The Command Center: what is happening, what needs attention, what to do next.
 *
 * ## The one rule this page obeys
 *
 * NEXUS runs ONE model — `microsoft/deberta-v3-base`, a fourteen-class intent
 * classifier. It maps one utterance to an intent name and a confidence. It is
 * not a language model: it cannot generate text, extract arguments, call a tool
 * or tell "delete a task" from "create a task". **Nothing on this page is
 * ordered, scored or predicted by it.** The ordering is the deterministic
 * arithmetic in `features/command-center/priority`, the counts are real endpoint
 * responses, and the classifier appears in exactly one panel, badged
 * `Model-derived`, where a prediction can only be read as a prediction.
 *
 * ## Every number has a real endpoint behind it
 *
 * | Panel | Source | Provenance |
 * | --- | --- | --- |
 * | Next up | `/risks`, `/recommendations`, `/tasks`, `/planner/conflicts`, `/learning/goals` | calculated order over measured records |
 * | Findings | `GET /risks/summary` | measured |
 * | Deadlines and blockers | `GET /activity/stats` | measured |
 * | Momentum | `GET /analytics/overview` | measured |
 * | The classifier | `GET /ml/status`, `POST /ml/route` | model-derived |
 * | Recorded engineering | `GET /developer/summary` | measured |
 * | Learning | `GET /learning/summary` | measured |
 *
 * Nothing is fabricated. A panel with no records renders its own empty state, a
 * figure the backend could not compute renders with the backend's own reason,
 * and a panel whose request failed renders its own error with a retry while
 * every other panel stays exactly as it was.
 *
 * ## Layout
 *
 * Four full-width rows from `xl`, one stacked column below it, each row a
 * `grid-cols-12` holding one wide panel beside one narrow one — the pairing
 * the dashboard and the analytics page already use. The rows are laid down in
 * the order the panels answer their question, so a phone reads top to bottom
 * in the same order a desktop does. The page reflows rather than merely
 * shrinking.
 */

/** Page size for each of the queue's sources. Small on purpose: this is a briefing. */
const LIST_LIMIT = 20

/** How far ahead the planner conflict read looks. */
const CONFLICT_HORIZON_DAYS = 14

/** Stable empty arrays so a failed source never re-creates the dependency list. */
const NO_RISKS: readonly RiskRead[] = []
const NO_RECOMMENDATIONS: readonly RecommendationRead[] = []
const NO_TASKS: readonly Task[] = []
const NO_CONFLICTS: readonly Conflict[] = []
const NO_GOALS: readonly LearningGoalRead[] = []

/** The slice of a `UseQueryResult` `panelState` and `feed` read. */
interface QueryLike {
  isPending: boolean
  isError: boolean
  error: unknown
  data: unknown
  refetch: () => unknown
}

export default function CommandCenterPage() {
  // One clock read for the whole render. `collectSignals` takes it as an
  // argument so a deadline bucket cannot move under an assertion between two
  // calls made in the same pass.
  const now = useMemo(() => new Date(), [])
  const today = useMemo(() => dateOnlyOf(now), [now])
  const horizon = useMemo(() => shiftDateOnly(now, CONFLICT_HORIZON_DAYS), [now])

  // ---------------------------------------------------------------- the queue
  const risks = useRisks({ status: 'active', limit: LIST_LIMIT })
  const recommendations = useRecommendations({ status: 'new', limit: LIST_LIMIT })
  // Two task reads, because `GET /tasks` takes one value per filter: a task due
  // on or before today is not the same question as one at critical priority, and
  // a client that filtered on both would silently lose one of them.
  const deadlines = useTasks({ status: 'todo', due_before: today, limit: LIST_LIMIT })
  const criticalTasks = useTasks({ status: 'todo', priority: 'critical', limit: LIST_LIMIT })
  const conflicts = usePlannerConflicts({ start: today, end: horizon })
  const goals = useGoals(horizon)

  const signals = useMemo(
    () =>
      collectSignals(
        {
          risks: risks.data?.items ?? NO_RISKS,
          recommendations: recommendations.data?.items ?? NO_RECOMMENDATIONS,
          deadlines: deadlines.data?.items ?? NO_TASKS,
          criticalTasks: criticalTasks.data?.items ?? NO_TASKS,
          conflicts: conflicts.data?.conflicts ?? NO_CONFLICTS,
          learningGoals: goals.data?.items ?? NO_GOALS,
        },
        now,
      ),
    [
      risks.data,
      recommendations.data,
      deadlines.data,
      criticalTasks.data,
      conflicts.data,
      goals.data,
      now,
    ],
  )

  // -------------------------------------------------------------- the counts
  const riskSummary = useRiskSummary()
  const workStats = useActivityStats()
  const overview = useOverview({})
  const developer = useDeveloperSummary()
  const learning = useLearningSummary()
  const ml = useMlStatus()

  const modKey = usesCommandKey() ? '⌘' : 'Ctrl'

  const queueFeeds: NamedFeed[] = [
    { label: 'Risk findings', query: risks },
    { label: 'Suggestions', query: recommendations },
    { label: 'Open deadlines', query: deadlines },
    { label: 'Critical tasks', query: criticalTasks },
    { label: 'Schedule conflicts', query: conflicts },
    { label: 'Learning goals', query: goals },
  ]
  const feed = feedState(queueFeeds)

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title="Command Center"
        eyebrow={
          <>
            <Radar className="size-3.5" aria-hidden="true" />
            What is happening · what needs you · what is next
          </>
        }
        description="One prioritised surface over every module. The order is deterministic arithmetic over counts a real endpoint returned — NEXUS's single model is an intent classifier, and it does not rank anything here."
        actions={<SearchEntry modKey={modKey} />}
      />

      {/*
        Rows, not two rails. Each row is its own `grid-cols-12` carrying one
        wide panel and one narrow one, which is how the dashboard and the
        analytics page pair theirs. Two fixed rails were not the same thing: a
        rail is only ever as tall as its own content, so the shorter of the two
        left the taller one running on beside an empty column — with three
        panels against five, the wide side ran out of content roughly a thousand
        pixels before the narrow side did, and the page read as half-finished.
        A row is as tall as its tallest panel and the pair shares it, so a row
        that is not level is short inside the shorter card rather than blank
        down a whole column.

        The pairing is chosen so the two panels in a row answer roughly the same
        amount at once, which is what keeps that difference small. The queue
        sits beside the classifier because they are the two panels that argue
        about the model — one says the order is arithmetic, the other is the
        model — and a reader meets both before moving on.
      */}
      <div className="space-y-6">
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
          <CommandCenterPanel
            title="Next up"
            provenance="calculated"
            className="xl:col-span-7"
            description="Everything waiting on a decision, ranked by a rule stated in full under the list. Open any row to see the arithmetic behind its score."
            state={{
              ...feed,
              empty: signals.length === 0 && feed.failures.length === 0,
              emptyState: <QueueEmptyState />,
            }}
          >
            {signals.length > 0 ? (
              <PriorityQueue signals={signals} />
            ) : (
              <p className="text-sm leading-relaxed text-muted-foreground">
                Nothing could be read from the sources behind this list, so there is nothing to
                rank. The failures are named below.
              </p>
            )}
            <FeedFailures failures={feed.failures} />
          </CommandCenterPanel>

          <CommandCenterPanel
            title="The classifier"
            provenance="model-derived"
            className="xl:col-span-5"
            description="NEXUS's only model. It classifies one sentence; it scores nothing on this page."
            state={{
              ...panelState(ml),
              empty: ml.data !== undefined && ml.data.enabled === false,
              emptyState: <ClassifierOffEmpty />,
            }}
          >
            {ml.data && <MlInsightPanel status={ml.data} />}
          </CommandCenterPanel>
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
          <CommandCenterPanel
            title="Findings"
            provenance="measured"
            className="xl:col-span-7"
            description="Live conditions the detection engine raised, counted by band."
            state={{
              ...panelState(riskSummary),
              empty: riskSummary.data !== undefined && riskSummary.data.total === 0,
              emptyState: <FindingsEmpty />,
            }}
          >
            {riskSummary.data && <FindingsPanelBody summary={riskSummary.data} />}
          </CommandCenterPanel>

          <CommandCenterPanel
            title="Momentum"
            provenance="measured"
            className="xl:col-span-5"
            description="Totals the analytics module aggregated from your own records."
            state={{
              ...panelState(overview),
              empty: overview.data !== undefined && overview.data.totals.length === 0,
              emptyState: <MomentumEmpty />,
            }}
          >
            {overview.data && <MomentumPanelBody overview={overview.data} />}
          </CommandCenterPanel>
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
          <CommandCenterPanel
            title="Deadlines and blockers"
            provenance="measured"
            className="xl:col-span-7"
            description="Counts across every open task bucket, from the activity statistics the work module already maintains."
            state={{
              ...panelState(workStats),
              empty: workStats.data !== undefined && workStats.data.tasks.total === 0,
              emptyState: <NoTasksEmpty />,
            }}
          >
            {workStats.data && <WorkStatsBody stats={workStats.data.tasks} />}
          </CommandCenterPanel>

          <CommandCenterPanel
            title="Recorded engineering"
            provenance="measured"
            className="xl:col-span-5"
            description="Commits and active days git recorded on this machine."
            state={{
              ...panelState(developer),
              empty: developer.data !== undefined && developer.data.has_data === false,
              emptyState: <EngineeringEmpty />,
            }}
          >
            {developer.data && <EngineeringPanelBody summary={developer.data} />}
          </CommandCenterPanel>
        </div>

        {/*
          The last row has no wide panel left to pair, so it is a plain
          two-column grid rather than a 12 — two halves fill the row exactly
          instead of leaving four columns of nothing at the foot of the page.
        */}
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <CommandCenterPanel
            title="Learning"
            provenance="measured"
            description="Goals, activities and minutes you recorded."
            state={{
              ...panelState(learning),
              empty: learning.data !== undefined && learning.data.has_data === false,
              emptyState: <LearningEmpty />,
            }}
          >
            {learning.data && <LearningPanelBody summary={learning.data} />}
          </CommandCenterPanel>

          {/*
            A `Card`, not a hand-rolled section. The panel beside it and every
            panel above it are all `CardHeader`/`CardTitle`/`CardContent`, and
            this one re-stated the same three class strings by hand — so the
            header spacing, the title's type ramp and the landmark role were
            three things that had to be kept in step by hand rather than
            inherited.
          */}
          <Card role="region" aria-labelledby="command-center-quick-actions">
            <CardHeader>
              <CardTitle id="command-center-quick-actions">Quick actions</CardTitle>
              <CardDescription>
                Each one posts to a real create endpoint and reports what the server made. A task
                is not offered here because creating one also needs a project, and guessing which
                project would be a fabrication.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <QuickActions />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

/* --------------------------------------------------------- learning goals */

/**
 * The queue's learning source: goals whose target date falls on or before the
 * horizon.
 *
 * Filtering by `target_before` rather than by status is deliberate on two
 * counts. The wire takes one status at a time, so a client asking for "the
 * active goals" would get one of five buckets and silently drop the rest; and a
 * goal with no target date scores zero on the deadline factor anyway, so
 * including it would add rows that cannot compete for attention. Goals without a
 * target date stay fully readable on the Learning surface.
 */
function useGoals(horizon: string) {
  return useLearningGoals({ target_before: horizon, limit: LIST_LIMIT })
}

/* ------------------------------------------------------------------ pieces */

/**
 * Opens the command palette rather than duplicating it.
 *
 * The palette owns its own focus: it focuses its input on mount, so setting the
 * store's `open` flag is the whole of "focus the palette". There is deliberately
 * no second search field on this page — a box filtering a different corpus would
 * be exactly the duplication this entry exists to avoid.
 */
function SearchEntry({ modKey }: { modKey: string }) {
  const open = () => useCommandPaletteStore.getState().setOpen(true)

  return (
    <Button type="button" variant="outline" size="sm" onClick={open}>
      <Search aria-hidden="true" />
      Search
      <Badge variant="outline" className="ml-1 font-mono font-normal">
        {modKey}K
      </Badge>
    </Button>
  )
}

function QueueEmptyState() {
  return (
    <EmptyState
      compact
      icon={ShieldCheck}
      title="Nothing is asking for a decision"
      description="No live finding, unanswered suggestion, open deadline, schedule conflict or active goal is waiting. That is a count of the records that exist — not a claim that nothing is wrong."
      action={
        <Button variant="outline" size="sm" asChild>
          <Link to="/tasks">Open tasks</Link>
        </Button>
      }
    />
  )
}

function FindingsEmpty() {
  return (
    <EmptyState
      compact
      icon={ShieldCheck}
      title="No live findings"
      description="The detection engine has not raised a condition it can judge. With too little history it produces no row at all, and it says so here rather than reporting zeroes that read as a clean bill of health."
    />
  )
}

function NoTasksEmpty() {
  return (
    <EmptyState
      compact
      icon={Compass}
      title="No tasks recorded"
      description="Every count here is zero because there is nothing in the table, not because something went unmeasured."
      action={
        <Button variant="outline" size="sm" asChild>
          <Link to="/tasks">Open tasks</Link>
        </Button>
      }
    />
  )
}

function MomentumEmpty() {
  return (
    <EmptyState
      compact
      icon={Compass}
      title="No aggregates yet"
      description="Analytics has not written a rollup for this window. The records behind it may well exist — the aggregate has simply never been built."
    />
  )
}

function ClassifierOffEmpty() {
  return (
    <EmptyState
      compact
      icon={Compass}
      title="The classifier is switched off"
      description="GET /ml/status reports enabled: false, so there is no model to describe and nothing to predict. Nothing else on this page depends on it — every other panel reads a database, not the model."
    />
  )
}

function EngineeringEmpty() {
  return (
    <EmptyState
      compact
      icon={Compass}
      title="No repositories registered"
      description="Register a local work tree on the Developer surface and a scan records what git already knows about it. Until then there is nothing to count."
      action={
        <Button variant="outline" size="sm" asChild>
          <Link to="/developer">Open Developer</Link>
        </Button>
      }
    />
  )
}

function LearningEmpty() {
  return (
    <EmptyState
      compact
      icon={Compass}
      title="Nothing recorded yet"
      description="Goals, skills and activities are all things you write down. Nothing here is estimated, and an empty account reads as an empty account rather than as a performance."
      action={
        <Button variant="outline" size="sm" asChild>
          <Link to="/learning">Open Learning</Link>
        </Button>
      }
    />
  )
}

function WorkStatsBody({ stats }: { stats: TaskStats }) {
  const rows = [
    { label: 'To do', value: stats.todo },
    { label: 'In progress', value: stats.in_progress },
    { label: 'Blocked', value: stats.blocked },
    { label: 'Overdue', value: stats.overdue },
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        {rows.map((row) => (
          <div key={row.label} className="rounded-md border border-border p-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
              {row.label}
            </p>
            <p className="mt-1 font-mono text-2xl font-semibold tabular-nums text-foreground">
              {row.value}
            </p>
          </div>
        ))}
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {stats.total} task{stats.total === 1 ? '' : 's'} in total, of which {stats.completed}{' '}
        completed and {stats.cancelled} cancelled. Overdue is counted across the open buckets and
        is not folded into any one of them.
      </p>
    </div>
  )
}

/* ------------------------------------------------------- panel composition */

/** The panel state for a single query. */
function panelState(query: QueryLike) {
  return {
    pending: query.isPending,
    error: query.isError ? query.error : null,
    retry: () => void query.refetch(),
  }
}

interface NamedFeed {
  label: string
  query: QueryLike
}

interface FeedFailure {
  label: string
  error: unknown
  retry: () => void
}

/**
 * Merges several queries into one panel state.
 *
 * The queue draws on six endpoints and any of them can fail on its own. While
 * *none* has answered the panel shows its skeleton; the moment one answers the
 * panel renders, because a briefing that omits one failing source is more useful
 * than no briefing. A panel fails as a whole only when every source did, and a
 * source that failed alongside successful ones is listed underneath the queue
 * with its own retry — a reader must be told which parts of the list are absent.
 */
function feedState(feeds: NamedFeed[]): PanelState & { failures: FeedFailure[] } {
  const failures = feeds
    .filter((feed) => feed.query.isError)
    .map((feed) => ({ label: feed.label, error: feed.query.error, retry: () => void feed.query.refetch() }))
  const answered = feeds.some((feed) => feed.query.data !== undefined)

  return {
    pending: !answered && failures.length < feeds.length,
    error: failures.length === feeds.length ? failures[0]?.error ?? null : null,
    retry: () => {
      for (const failure of failures) failure.retry()
    },
    failures,
  }
}

/**
 * The sources that could not be read, reported rather than swallowed.
 *
 * Each row names the endpoint family that is missing and offers a retry that
 * asks again for that source alone. Rendering nothing here would let a partial
 * briefing pass for a complete one.
 */
function FeedFailures({ failures }: { failures: FeedFailure[] }) {
  if (failures.length === 0) return null

  return (
    <div className="mt-4 space-y-2 border-t border-border pt-4">
      <p className="text-xs font-medium text-foreground">
        {failures.length} of the sources behind this list could not be read, so it is incomplete.
      </p>
      {failures.map((failure) => (
        <div
          key={failure.label}
          role="alert"
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/30 bg-destructive/[0.04] px-3 py-2"
        >
          <span className="text-xs leading-relaxed text-foreground/80">
            <span className="font-medium text-foreground">{failure.label}:</span>{' '}
            {toApiError(failure.error).message}
          </span>
          <Button variant="outline" size="sm" onClick={failure.retry}>
            Retry
          </Button>
        </div>
      ))}
    </div>
  )
}
