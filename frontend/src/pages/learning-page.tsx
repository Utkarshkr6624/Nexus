import { cloneElement, isValidElement, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent, ReactElement, ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CalendarClock, GraduationCap, Lightbulb, Play, Plus, Sparkles, Target } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LiveStatus } from '@/components/feedback/live-status'
import { LoadingState } from '@/components/feedback/loading-state'
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
import { AnalyticsBarChart, LazyChart } from '@/features/analytics/components/lazy-charts'
import { formatNumber } from '@/features/analytics/format'
import {
  ACTIVITY_TYPE_META,
  ACTIVITY_TYPE_ORDER,
  GOAL_PRIORITY_META,
  LearningActivitySummary,
  LearningActivityTimeline,
  LearningGoalCardGrid,
  LearningStaleNotice,
  LearningTimelineScopeNote,
  SkillCardGrid,
  SkillGapList,
  describeTargetDate,
  describeWindow,
} from '@/features/learning/components'
import {
  LEARNING_WINDOW_PRESETS,
  useCreateLearningActivity,
  useCreateLearningGoal,
  useCreateSkill,
  useEvaluateLearningRecommendations,
  useLearningActivities,
  useLearningActivity,
  useLearningGoals,
  useLearningSummary,
  useLearningWindow,
  useSkillGaps,
  useSkills,
} from '@/features/learning/hooks'
import type { LearningWindow, LearningWindowPresetId } from '@/features/learning/hooks'
import type { ApiError } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { toApiError, bannerError, fieldErrorMessages } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { todayDateOnly } from '@/types/analytics'
import type { Granularity } from '@/types/analytics'
import type {
  LearningActivityRead,
  LearningActivitySeriesRead,
  LearningActivityType,
  LearningGoalRead,
  SkillRead,
  UUIDString,
} from '@/types/learning'
import type { RecommendationRead } from '@/types/risk'
import type { ProjectPriority } from '@/types/work'

/**
 * The Learning dashboard.
 *
 * ## What this page is allowed to say
 *
 * Everything below is a **count of something a record holds** — goals written
 * down, skills tracked, learning activities recorded, minutes somebody attached
 * to those activities. There is no tile for hours of effort, retention,
 * comprehension or progress, and the reason is not editorial caution: a git
 * timestamp, a note body or a resource view cannot prove any of those, so a
 * figure for them could not be computed here even if a card wanted one. The
 * one figure on this page that looks like a score — `progress` — is the user's
 * own percentage, and the goal card says so underneath the bar on every card.
 *
 * ## A level is never a bare number
 *
 * `SkillCardGrid` renders every level through `describeLevelClaim` beside a
 * `LevelSourceBadge` that cannot be switched off, so a reader always sees
 * whether the number is one they set or one NEXUS estimated from recorded
 * activities. The gap list does the same, and adds the backend's own
 * `explanation` verbatim rather than a paraphrase that could lose the evidence
 * count.
 *
 * ## Placeholder data is disclosed, never passed off as current
 *
 * Every window-shaped read carries `placeholderData: (previous) => previous`, so
 * switching the range keeps the previous window on screen instead of blanking the
 * page. Those figures are real but they are the *previous* answer, so every
 * region is handed `isStale` and renders the refreshing line rather than showing
 * them as if they were current.
 *
 * **Everything view-shaped lives in the URL.** `?range=` (with `?start=` and
 * `?end=` for a custom span) comes from `useLearningWindow`, which sends *no*
 * `window_days` at all for the default preset so the backend applies its own
 * configured value; `?skill=` narrows the recorded trail; `?granularity=`
 * re-buckets the activity series; and `?goal_page=`, `?skill_page=` and
 * `?activity_page=` hold one page each of the three lists. The window caption
 * quotes the window the server echoed back rather than the one the client asked
 * for, because those can differ and only the server knows — and the grain
 * caption does the same with the bucket width, so a caption can never outlive
 * the filter that produced the picture above it.
 *
 * ## One chart is deliberately not windowed, and says so on its own card
 *
 * `Recorded activity by skill` is drawn from each skill's all-time
 * `evidence_count`, so it sits in its own labelled section rather than beside
 * the windowed panels: a chart that quietly showed a different range from
 * everything around it would be the one dishonest figure on a page whose whole
 * argument is that every number names its own scope.
 *
 * ## Paging is the reader's, and a page is never mistaken for the whole
 *
 * Every list below the window is server-paginated, so each carries a pager that
 * reads and writes the URL and disables itself at both boundaries. A pager is
 * rendered only when there is more than one page — two disabled buttons under a
 * list that already fits is noise. The counts beside the pagers are the
 * backend's `total`, never the length of the page on screen.
 *
 * **The pickers and the name lookups are not paginated.** A filter or a form
 * that could only offer the rows on the current page would hide the rest of the
 * account, and an activity whose goal was on another page would render its goal
 * as "no longer tracked" when it had not been deleted at all. So the skill and
 * goal *pickers* read the whole set in one bounded request each, while the
 * cards, the chart and the trail page through the URLs above. That is two
 * requests, not one per row.
 */
/**
 * One page of goals, one page of skills, one page of recorded activity.
 *
 * Twenty is small enough that the pager is real rather than decorative — the
 * backend will hold up to 200 goals and 100 skills — and large enough that the
 * common account never sees the control at all.
 */
const GOAL_PAGE_SIZE = 20
const SKILL_PAGE_SIZE = 20
const ACTIVITY_PAGE_SIZE = 12

/**
 * What the pickers read: the API's own page ceiling, which is also larger than
 * either account ceiling (`learning_max_goals` and `learning_max_skills`) on a
 * default deployment. Anything above it is a 422, so this is the largest legal
 * complete read rather than a number chosen for taste.
 */
const PICKER_LIMIT = 200

const GOAL_PAGE_PARAM = 'goal_page'
const SKILL_PAGE_PARAM = 'skill_page'
const ACTIVITY_PAGE_PARAM = 'activity_page'

/** An absent `?skill=` already means "every skill", so "All" needs a word. */
const ALL_SKILLS = 'all'

/** The three bucket widths the endpoint accepts, finest first. */
const LEARNING_GRAINS: readonly Granularity[] = ['day', 'week', 'month']

/** Short names for the segmented control; the sentences below carry the sense. */
const GRAIN_LABEL: Record<Granularity, string> = { day: 'Day', week: 'Week', month: 'Month' }

/** Names the segmented control, and is referenced by the radiogroup's label. */
const GRAIN_LABEL_ID = 'learning-grain-label'

/** Stable empty arrays, so the memos below are not re-created every render. */
const NO_GOALS: LearningGoalRead[] = []
const NO_SKILLS: SkillRead[] = []
const NO_ACTIVITIES: LearningActivityRead[] = []

const GOAL_PRIORITIES: readonly ProjectPriority[] = ['low', 'medium', 'high', 'critical']

/**
 * The page a `?<param>=` value names.
 *
 * Anything that is not a whole number above one is the first page, and the first
 * page is written *absent* rather than as `=1`. A link to the default view and a
 * link to page one then mean the same thing, which is what keeps a shared URL
 * honest.
 */
function pageFrom(params: URLSearchParams, key: string): number {
  const page = Number.parseInt(params.get(key) ?? '', 10)
  return Number.isFinite(page) && page > 1 ? page : 1
}

/** How many pages a `total` is worth, or null while the read has not answered. */
function pageCount(total: number | null, size: number): number | null {
  if (total === null || !Number.isFinite(total)) return null
  return Math.max(1, Math.ceil(Math.max(0, total) / size))
}

/**
 * Keeps `?<param>=` inside the number of pages the backend says exist.
 *
 * A URL can arrive at a page the data no longer has: a link shared from a
 * six-month window, a window shortened, a skill filter that matches two rows.
 * The pager's buttons would then sit disabled on both sides of a page that does
 * not exist, so the param is dropped and the list returns to the first page.
 * Deleting rather than clamping to the last page is the deliberate choice —
 * the filter behind the position changed, and the first page is the one that
 * cannot be a stale answer to it.
 */
function useClampPage(param: string, page: number, pages: number | null): void {
  const [searchParams, setSearchParams] = useSearchParams()

  useEffect(() => {
    if (pages === null || page <= pages) return
    const next = new URLSearchParams(searchParams)
    next.delete(param)
    setSearchParams(next, { replace: true })
  }, [param, page, pages, searchParams, setSearchParams])
}

/**
 * The sentence that ties the activity chart to the grain it was drawn at.
 *
 * **It quotes the response, not the request**, for the same reason the window
 * caption does: only the server knows which buckets it produced. While a
 * re-bucketing read is in flight the picture on screen is still the previous
 * one, so the caption says that instead of naming a grain the chart does not
 * have — a caption that outlived its filter would be worse than no caption.
 */
function seriesCaption(
  series: LearningActivitySeriesRead | null | undefined,
  isStale: boolean,
): string {
  if (!series || isStale) {
    return 'The activity chart is being redrawn at the grain chosen here.'
  }
  return (
    `The activity chart below plots one point per ${series.granularity} across ` +
    `${describeWindow(series.window_days)}. Every bucket in that range is plotted, ` +
    'including the ones with nothing recorded in them.'
  )
}

export default function LearningPage() {
  const window = useLearningWindow()
  const [searchParams, setSearchParams] = useSearchParams()
  const [goalFormOpen, setGoalFormOpen] = useState(false)
  const [activityFormOpen, setActivityFormOpen] = useState(false)
  const [skillFormOpen, setSkillFormOpen] = useState(false)

  const skillParam = searchParams.get('skill')
  const skillFilter = skillParam ?? ALL_SKILLS

  const goalPage = pageFrom(searchParams, GOAL_PAGE_PARAM)
  const skillPage = pageFrom(searchParams, SKILL_PAGE_PARAM)
  const activityPage = pageFrom(searchParams, ACTIVITY_PAGE_PARAM)

  const summary = useLearningSummary(window.params)
  const series = useLearningActivity(window.activityParams)
  const gaps = useSkillGaps(window.params)
  const goals = useLearningGoals({ limit: GOAL_PAGE_SIZE, offset: (goalPage - 1) * GOAL_PAGE_SIZE })
  const skills = useSkills({ limit: SKILL_PAGE_SIZE, offset: (skillPage - 1) * SKILL_PAGE_SIZE })
  const activities = useLearningActivities({
    limit: ACTIVITY_PAGE_SIZE,
    offset: (activityPage - 1) * ACTIVITY_PAGE_SIZE,
    window_days: window.window_days,
    skill_id: skillFilter === ALL_SKILLS ? undefined : skillFilter,
  })

  /**
   * The complete goal and skill sets, for the things a page of cards cannot
   * answer.
   *
   * Three of them: the deadlines panel, which must list every open goal that
   * carries a date rather than the ones that happened to land on page two; the
   * pickers in all three forms and the trail's skill filter, which would
   * otherwise offer only the rows currently on screen; and the name lookups
   * behind the trail, where an unresolvable `goal_id` renders as "no longer
   * tracked" — a sentence about a deletion that did not happen.
   *
   * One bounded request each. A windowed per-skill count would need one request
   * per skill, and a page that fires 40 of them to draw one chart is a page
   * whose cost nobody can predict.
   */
  const everyGoal = useLearningGoals({ limit: PICKER_LIMIT, offset: 0 })
  const everySkill = useSkills({ limit: PICKER_LIMIT, offset: 0 })

  const goalPages = pageCount(goals.data?.total ?? null, GOAL_PAGE_SIZE)
  const skillPages = pageCount(skills.data?.total ?? null, SKILL_PAGE_SIZE)
  const activityPages = pageCount(activities.data?.total ?? null, ACTIVITY_PAGE_SIZE)
  useClampPage(GOAL_PAGE_PARAM, goalPage, goalPages)
  useClampPage(SKILL_PAGE_PARAM, skillPage, skillPages)
  useClampPage(ACTIVITY_PAGE_PARAM, activityPage, activityPages)

  const apply = useCallback(
    (patch: Record<string, string | undefined>) => {
      const next = new URLSearchParams(searchParams)
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined || value === ALL_SKILLS) next.delete(key)
        else next.set(key, value)
      }
      setSearchParams(next)
    },
    [searchParams, setSearchParams],
  )

  /** Writes a page position. Page one is absent from the URL, not `=1`. */
  const applyPage = useCallback(
    (param: string, page: number) => apply({ [param]: page > 1 ? String(page) : undefined }),
    [apply],
  )

  const skillRows = skills.data?.items ?? NO_SKILLS
  const goalRows = goals.data?.items ?? NO_GOALS
  const activityRows = activities.data?.items ?? NO_ACTIVITIES
  const everyGoalRows = everyGoal.data?.items ?? NO_GOALS
  const everySkillRows = everySkill.data?.items ?? NO_SKILLS

  const skillNames = useMemo(() => {
    const map: Record<UUIDString, string> = {}
    for (const skill of everySkillRows) map[skill.id] = skill.name
    return map
  }, [everySkillRows])

  const goalNames = useMemo(() => {
    const map: Record<UUIDString, string> = {}
    for (const goal of everyGoalRows) map[goal.id] = goal.title
    return map
  }, [everyGoalRows])

  /**
   * Open and finished goals, split here rather than by two filtered requests.
   *
   * `status` is an exact-match filter server-side, so "still open" is
   * `not_started`, `in_progress` and `paused` together and cannot be asked for
   * as one query. Two requests would each page independently and the two
   * sections would claim different totals; one read partitioned by the same
   * predicate the summary uses keeps them agreeing.
   *
   * `archived` is counted separately and never joined to `completed`: an
   * archived goal is a record the user has set aside, and rendering it beside a
   * finished one would say "3 goals remaining" on an account that finished
   * them all.
   */
  const { activeGoals, completedGoals } = useMemo(() => {
    const open: LearningGoalRead[] = []
    const done: LearningGoalRead[] = []
    for (const goal of goalRows) {
      if (goal.status === 'completed') done.push(goal)
      else if (goal.status !== 'archived') open.push(goal)
    }
    return { activeGoals: open, completedGoals: done }
  }, [goalRows])

  /**
   * Archived goals across the whole account, not across the page.
   *
   * The sentence under the completed list says how many were left out, so it
   * has to be counting the same set the backend's `goal_count` does. Counting
   * one page would make it a number about the pager.
   */
  const archivedCount = useMemo(
    () => everyGoalRows.filter((goal) => goal.status === 'archived').length,
    [everyGoalRows],
  )

  /**
   * Deadlines, soonest first, and only the ones still ahead of today.
   *
   * A goal with no `target_date` has **no deadline** — a legitimate state — so
   * it is left out of this list rather than pushed to the bottom with a
   * "0 days" row. A date already behind today is not a verdict about the plan
   * either, so it is counted separately and named as "already past" instead of
   * being called overdue.
   *
   * **Built from the complete goal set, not the page.** "Soonest first" is only
   * true of a list that contains every goal, and a deadline that sat on page
   * three would otherwise not be approaching anything on screen.
   */
  const { upcoming, pastCount } = useMemo(() => {
    const today = todayDateOnly()
    const ahead: LearningGoalRead[] = []
    let past = 0
    for (const goal of everyGoalRows) {
      if (goal.status === 'completed' || goal.status === 'archived') continue
      if (!goal.target_date) continue
      if (goal.target_date >= today) ahead.push(goal)
      else past += 1
    }
    ahead.sort((left, right) => (left.target_date ?? '').localeCompare(right.target_date ?? ''))
    return { upcoming: ahead, pastCount: past }
  }, [everyGoalRows])

  /**
   * The per-type breakdown, but only when the rows on screen are the whole
   * window.
   *
   * The activity trail is paginated, so counting `activity_type` across one
   * page would report fewer events than the window holds while reading as the
   * whole. When the backend's `total` exceeds the rows fetched the breakdown
   * is withheld rather than shown as a partial count dressed as a complete one.
   */
  const byType = useMemo<Record<string, number> | null>(() => {
    const list = activities.data
    if (!list || list.total !== list.items.length) return null
    const counts: Record<string, number> = {}
    for (const activity of list.items) {
      const key = activity.activity_type
      counts[key] = (counts[key] ?? 0) + 1
    }
    return counts
  }, [activities.data])

  /**
   * Recorded activity per skill, all time.
   *
   * Read from `SkillRead.evidence_count` — the number of learning activities
   * recorded against that skill, which is a measurement of records rather than
   * of ability. It is **not** a windowed figure and does not pretend to be: the
   * chart sits in a section of its own that says so, and it is built from the
   * complete skill set so "each tracked skill" is true of the whole account
   * rather than of whichever page the reader happens to be on. A windowed
   * per-skill count would need one request per skill, and a chart that quietly
   * mixed two ranges would be worse than none.
   */
  const bySkill = useMemo(
    () =>
      everySkillRows
        .map((skill) => ({ label: skill.name, activities: skill.evidence_count }))
        .sort((left, right) => right.activities - left.activities),
    [everySkillRows],
  )

  const isStale = (query: { isPlaceholderData: boolean; isFetching: boolean; isPending: boolean }) =>
    query.isPlaceholderData || (query.isFetching && !query.isPending)

  const windowDays = summary.data?.window_days ?? series.data?.window_days ?? null

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title="Learning"
        eyebrow={
          <>
            <GraduationCap className="size-3.5" aria-hidden="true" />
            Goals, skills and recorded evidence
          </>
        }
        badges={
          summary.data ? (
            <span className="text-xs text-muted-foreground">
              {summary.data.has_data
                ? `Figures cover ${describeWindow(summary.data.window_days)}`
                : 'Nothing recorded yet'}
            </span>
          ) : null
        }
        actions={
          <>
            <Button type="button" variant="outline" onClick={() => setSkillFormOpen(true)}>
              <Sparkles aria-hidden="true" />
              Add a skill
            </Button>
            <Button type="button" variant="outline" onClick={() => setActivityFormOpen(true)}>
              <Plus aria-hidden="true" />
              Record activity
            </Button>
            <Button type="button" onClick={() => setGoalFormOpen(true)}>
              <Plus aria-hidden="true" />
              New goal
            </Button>
          </>
        }
        description="Everything on this page is read from records you create — goals, skills and the learning activities you log. A skill level is always shown with where it came from: one you set, or one NEXUS estimated from recorded activity. Nothing here is a judgement about how good you are at something, and NEXUS never writes a level, a deadline or a progress figure on your behalf."
      />

      <WindowBar window={window} caption={seriesCaption(series.data, isStale(series))} />

      <section aria-labelledby="learning-overview" className="space-y-3">
        <h2 id="learning-overview" className="sr-only">
          Overview
        </h2>
        <LearningActivitySummary
          summary={summary.data ?? null}
          series={series.data ?? null}
          byType={byType}
          isLoading={summary.isPending && !summary.data}
          isStale={isStale(summary)}
          error={summary.isError && !summary.isPlaceholderData ? toApiError(summary.error) : null}
          onRetry={() => void summary.refetch()}
          titleLevel="h3"
        />
      </section>

      <DeadlinesPanel
        upcoming={upcoming}
        pastCount={pastCount}
        isLoading={everyGoal.isPending && !everyGoal.data}
        isStale={everyGoal.isPlaceholderData}
        error={
          everyGoal.isError && !everyGoal.isPlaceholderData ? toApiError(everyGoal.error) : null
        }
        onRetry={() => void everyGoal.refetch()}
      />

      <SuggestionsSweep />

      <section aria-labelledby="learning-goals" className="space-y-4">
        <h2 id="learning-goals" className="text-base font-semibold text-foreground">
          Goals
        </h2>

        <div className="min-w-0 space-y-2">
          <h3 className="text-sm font-medium text-foreground">Still open</h3>
          <p className="text-xs text-muted-foreground">
            Not completed and not archived. Every progress figure on these cards is the one you set.
          </p>
          <LearningGoalCardGrid
            goals={activeGoals}
            isLoading={goals.isPending && !goals.data}
            isStale={goals.isPlaceholderData}
            error={goals.isError && !goals.isPlaceholderData ? toApiError(goals.error) : null}
            onRetry={() => void goals.refetch()}
            skillNames={skillNames}
            emptyReason={
              summary.data && summary.data.goal_count > 0
                ? 'Goals exist on this account; the ones fetched here do not include any that are still open. The counts above cover every goal you own.'
                : null
            }
            emptyAction={
              <Button type="button" onClick={() => setGoalFormOpen(true)}>
                <Plus aria-hidden="true" />
                Write down a goal
              </Button>
            }
            skeletonCount={3}
            titleLevel="h4"
          />
        </div>

        <div className="min-w-0 space-y-2">
          <h3 className="text-sm font-medium text-foreground">Completed</h3>
          <p className="text-xs text-muted-foreground">
            Finished goals, kept as a record.
            {archivedCount > 0
              ? ` ${archivedCount} archived ${archivedCount === 1 ? 'goal is' : 'goals are'} not listed here and not counted among the goals still open.`
              : ''}
          </p>
          <LearningGoalCardGrid
            goals={completedGoals}
            isLoading={goals.isPending && !goals.data}
            isStale={goals.isPlaceholderData}
            error={goals.isError && !goals.isPlaceholderData ? toApiError(goals.error) : null}
            onRetry={() => void goals.refetch()}
            skillNames={skillNames}
            emptyReason={
              activeGoals.length > 0
                ? 'No goal has been marked complete yet. Completion is stamped by the server when you mark one, and it is never inferred from how much activity a goal has seen.'
                : null
            }
            skeletonCount={2}
            titleLevel="h4"
          />
        </div>

        {goals.data && goalPages !== null && (
          <Pager
            label="Goal pages"
            page={goalPage}
            pages={goalPages}
            total={goals.data.total}
            nouns={{ one: 'goal', many: 'goals' }}
            onPage={(next) => applyPage(GOAL_PAGE_PARAM, next)}
          />
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <SkillGapList
          gaps={gaps.data ?? []}
          windowDays={windowDays}
          isLoading={gaps.isPending && !gaps.data}
          isStale={isStale(gaps)}
          error={gaps.isError && !gaps.isPlaceholderData ? toApiError(gaps.error) : null}
          onRetry={() => void gaps.refetch()}
          emptyAction={
            <Button type="button" onClick={() => setSkillFormOpen(true)}>
              <Sparkles aria-hidden="true" />
              Add a skill
            </Button>
          }
          titleLevel="h3"
          subtitle="Each gap is computed on read from the level you set and the target beside it, never stored, and the sentence under every row names both levels and the number of recorded activities behind them."
        />

        {/*
          * The one chart on this page that is not windowed, in a section of its
          * own rather than beside a windowed panel. Its heading and its sentence
          * both say what it covers, so a reader cannot take it as a figure from
          * the selected window — and the range it does cover is named, not left
          * to be guessed from a subtitle.
          */}
        <section aria-labelledby="learning-by-skill-all-time" className="min-w-0 space-y-2">
          <h3 id="learning-by-skill-all-time" className="text-sm font-medium text-foreground">
            Recorded activity by skill — all time
          </h3>
          <p className="text-xs leading-relaxed text-muted-foreground">
            This is the one figure on the page that ignores the window and the grain above. It counts
            every learning activity ever recorded against each tracked skill, so neither control
            changes it, and it covers every skill on the account rather than the ones on this page.
          </p>
          <QueryGate
            query={everySkill}
            label="Loading the tracked skills"
            title="The skills could not load"
            onRetry={() => void everySkill.refetch()}
          >
            {() => (
              <LazyChart
                title="Recorded activity by skill"
                subtitle="Learning activities recorded against each tracked skill, all time. A skill with nothing recorded against it shows zero, which is a measurement rather than a missing value."
              >
                <AnalyticsBarChart
                  title="Recorded activity by skill"
                  subtitle="Learning activities recorded against each tracked skill, all time."
                  orientation="horizontal"
                  colorByCategory
                  data={bySkill}
                  series={[{ key: 'activities', label: 'Activities recorded', unit: 'count' }]}
                  isEmpty={bySkill.length === 0}
                  emptyMetric="learning"
                  emptyReason="Track a skill first. Once one exists, the learning activities you record against it are counted here."
                />
              </LazyChart>
            )}
          </QueryGate>
        </section>
      </div>

      <section aria-labelledby="learning-skills" className="space-y-3">
        <h2 id="learning-skills" className="text-base font-semibold text-foreground">
          Skills
        </h2>
        <p className="text-xs text-muted-foreground">
          Each level is shown beside where it came from: one you set, or one NEXUS estimated from
          recorded activity. The evidence count under it is a count of records, never of ability.
        </p>
        <SkillCardGrid
          skills={skillRows}
          isLoading={skills.isPending && !skills.data}
          isStale={skills.isPlaceholderData}
          error={skills.isError && !skills.isPlaceholderData ? toApiError(skills.error) : null}
          onRetry={() => void skills.refetch()}
          emptyAction={
            <Button type="button" onClick={() => setSkillFormOpen(true)}>
              <Sparkles aria-hidden="true" />
              Add a skill
            </Button>
          }
          skeletonCount={6}
          titleLevel="h3"
        />

        {skills.data && skillPages !== null && (
          <Pager
            label="Skill pages"
            page={skillPage}
            pages={skillPages}
            total={skills.data.total}
            nouns={{ one: 'skill', many: 'skills' }}
            onPage={(next) => applyPage(SKILL_PAGE_PARAM, next)}
          />
        )}
      </section>

      <section aria-labelledby="learning-trail" className="space-y-3">
        <div className="min-w-0 space-y-1">
          <h2 id="learning-trail" className="text-base font-semibold text-foreground">
            Recorded activity
          </h2>
          <p className="text-xs text-muted-foreground">
            Every entry is an event that was recorded — a study session, a completed task, a note, a
            concept, or a resource that was opened. Opening a page is the weakest of these and is
            never merged with a concept recorded.
          </p>
        </div>

        <div className="rounded-lg border border-border bg-card p-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="app-form-field">
              <Label htmlFor="learning-skill-filter">Skill</Label>
              <Select
                id="learning-skill-filter"
                value={skillFilter}
                onChange={(event) =>
                  // The filter redefines the whole trail, so the page position
                  // it was read at is dropped with it rather than left pointing
                  // into a result that no longer exists.
                  apply({ skill: event.target.value, [ACTIVITY_PAGE_PARAM]: undefined })
                }
              >
                <option value={ALL_SKILLS}>All skills</option>
                {everySkillRows.map((skill) => (
                  <option key={skill.id} value={skill.id}>
                    {skill.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <LiveStatus active={activities.isPlaceholderData} className="mt-3 text-xs text-muted-foreground">
            Updating for the selected window…
          </LiveStatus>
        </div>

        <LearningActivityTimeline
          activities={activityRows}
          isLoading={activities.isPending && !activities.data}
          isStale={isStale(activities)}
          error={activities.isError && !activities.isPlaceholderData ? toApiError(activities.error) : null}
          onRetry={() => void activities.refetch()}
          skillName={(id) => skillNames[id] ?? null}
          goalName={(id) => goalNames[id] ?? null}
          total={activities.data?.total ?? null}
          title="Recent sessions and events"
          titleLevel="h3"
          subtitle="Newest first. Every row names the skill or goal it belongs to and where the entry came from."
          emptyAction={
            <Button type="button" onClick={() => setActivityFormOpen(true)}>
              <Plus aria-hidden="true" />
              Record an activity
            </Button>
          }
        />
        {!activities.isPending && !activities.isError && (
          <LearningTimelineScopeNote
            total={activities.data?.total ?? null}
            shown={activityRows.length}
          />
        )}

        {activities.data && activityPages !== null && (
          <Pager
            label="Activity pages"
            page={activityPage}
            pages={activityPages}
            total={activities.data.total}
            nouns={{ one: 'recorded activity', many: 'recorded activities' }}
            onPage={(next) => applyPage(ACTIVITY_PAGE_PARAM, next)}
          />
        )}
      </section>

      <NewGoalDialog open={goalFormOpen} onOpenChange={setGoalFormOpen} skills={everySkillRows} />

      <RecordActivityDialog
        open={activityFormOpen}
        onOpenChange={setActivityFormOpen}
        skills={everySkillRows}
        goals={everyGoalRows}
        defaultSkillId={skillFilter === ALL_SKILLS ? '' : skillFilter}
      />

      <AddSkillDialog open={skillFormOpen} onOpenChange={setSkillFormOpen} />
    </div>
  )
}

/* ------------------------------------------------------------------ plumbing */

type QueryLike<T> = {
  data: T | undefined
  error: unknown
  isPending: boolean
  refetch: () => unknown
}

/**
 * The three states every region shares: loading, failed, loaded.
 *
 * The failed case goes through `ErrorState` with a working retry rather than a
 * bare "Something went wrong", because a failed read is otherwise
 * indistinguishable from a region that is merely empty.
 */
function QueryGate<T>({
  query,
  label,
  title,
  onRetry,
  children,
}: {
  query: QueryLike<T>
  label: string
  title: string
  onRetry: () => void
  children: (data: T) => ReactNode
}) {
  if (query.isPending) return <LoadingState label={label} />
  if (query.error) return <ErrorState error={toApiError(query.error)} title={title} onRetry={onRetry} />
  if (query.data === undefined) return <LoadingState label={label} compact />
  return <>{children(query.data)}</>
}

/**
 * The two view controls the activity chart answers to: the window presets and
 * the bucket grain.
 *
 * `Custom` is deliberately **not** rendered. It would be a control that opened
 * no inputs and left the range unchanged, and a picker that offers a choice it
 * cannot honour is worse than one that does not offer it. A link carrying
 * `?start=` and `?end=` still works — the hook resolves the length from them —
 * so nothing is lost but the empty control.
 *
 * **Both captions quote the response, not the request.** The default preset
 * sends no `window_days` at all so the backend applies its own
 * `learning_default_window_days`, and the grain is whatever buckets the server
 * chose to build. Printing the client's own guess would be a caption the server
 * cannot back, and a caption that says "by week" over a daily chart is worse
 * than no caption at all.
 *
 * **The caption sits with the control that produced it**, so the two are
 * recomputed from one state on every render and cannot drift apart: a chart
 * whose caption describes a filter it is no longer under is the failure this
 * whole page is written to avoid.
 */
function WindowBar({ window, caption }: { window: LearningWindow; caption: string }) {
  return (
    <section className="space-y-3" aria-labelledby="learning-window">
      <h2 id="learning-window" className="sr-only">
        Window and grain
      </h2>
      <div className="rounded-lg border border-border bg-card p-3">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
          <div className="space-y-1.5">
            <p className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
              Window
            </p>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Window presets">
              {LEARNING_WINDOW_PRESETS.filter((preset) => preset.id !== 'custom').map((preset) => {
                const selected = window.preset === preset.id
                return (
                  <Button
                    key={preset.id}
                    type="button"
                    size="sm"
                    variant={selected ? 'default' : 'outline'}
                    aria-pressed={selected}
                    onClick={() => window.setPreset(preset.id as LearningWindowPresetId)}
                  >
                    {preset.label}
                  </Button>
                )
              })}
            </div>
          </div>

          <div className="space-y-1.5">
            <p
              id={GRAIN_LABEL_ID}
              className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground"
            >
              Grain
            </p>
            <GranularityControl value={window.granularity} onChange={window.setGranularity} />
          </div>
        </div>

        <p className="mt-3 text-xs text-muted-foreground">
          {caption}
        </p>

        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          A learning window is a trailing span of days, resolved backwards from today by the server.
          The summaries, the gap explanations and the recorded trail are all computed over it, and
          each of them says so in its own sentences. One chart on this page is the exception, and it
          carries its own label saying which range it covers.
        </p>

        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          Grain regroups the same recorded activities into wider buckets. It filters nothing out: the
          totals behind the chart are the same whichever grain you choose, only the shape of the line
          changes.
        </p>
      </div>
    </section>
  )
}

/**
 * Day / week / month, as an exclusive choice.
 *
 * **Roving tabindex, per the radiogroup pattern** — the same one the developer
 * surface's activity control uses. Exactly one option sits in the tab order;
 * the arrows move between them and wrap, and Home and End jump to the ends.
 * Without it a keyboard user tabs through three controls to change one thing and
 * never hears which one is selected.
 *
 * **Moving the arrow selects**, which is what makes this a radiogroup rather
 * than three buttons: the pressed option and the chart on screen are always the
 * same choice, because one key press puts them there together. Only the grain
 * changes — the window is carried through by the hook, so widening the range
 * does not quietly undo a grain the reader already chose.
 */
function GranularityControl({
  value,
  onChange,
}: {
  value: Granularity
  onChange: (granularity: Granularity) => void
}) {
  const options = useRef<Array<HTMLButtonElement | null>>([])

  function move(event: KeyboardEvent<HTMLButtonElement>, index: number): void {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return
    const last = LEARNING_GRAINS.length - 1
    let next: number
    if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = last
    else if (event.key === 'ArrowRight') next = index === last ? 0 : index + 1
    else next = index === 0 ? last : index - 1

    const target = LEARNING_GRAINS[next]
    if (!target) return
    event.preventDefault()
    onChange(target)
    options.current[next]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-labelledby={GRAIN_LABEL_ID}
      className="inline-flex items-stretch gap-0.5 rounded-md border border-border p-0.5"
    >
      {LEARNING_GRAINS.map((grain, index) => {
        const selected = grain === value
        return (
          <button
            key={grain}
            ref={(node) => {
              options.current[index] = node
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(grain)}
            onKeyDown={(event) => move(event, index)}
            className={cn(
              'rounded px-3 py-1 text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              selected ? 'bg-primary/15 text-primary' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {GRAIN_LABEL[grain]}
          </button>
        )
      })}
    </div>
  )
}

/**
 * One pager: where the reader is, how many pages there are, and both boundaries.
 *
 * **It renders nothing when there is a single page.** Two disabled buttons
 * under a list that already fits is noise, and the rows plus the count beside
 * them already say that the whole set is on screen.
 *
 * The count is the backend's `total` — the size of the filtered set — rather
 * than the length of the page, because a pager that quoted its own page as the
 * total is the mistake this page is built to avoid. `pages` is null while the
 * read is unanswered, and then nothing renders rather than a guess.
 */
function Pager({
  label,
  page,
  pages,
  total,
  nouns,
  onPage,
}: {
  label: string
  page: number
  pages: number | null
  total: number
  /** Singular and plural stated together: `activity`/`activities` is not a
   *  matter of appending an `s`. */
  nouns: { one: string; many: string }
  onPage: (next: number) => void
}) {
  if (pages === null || pages <= 1) return null

  return (
    <nav aria-label={label} className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs text-muted-foreground">
        Page {formatNumber(page)} of {formatNumber(pages)} · {formatNumber(total)}{' '}
        {total === 1 ? nouns.one : nouns.many}
      </p>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          Next
        </Button>
      </div>
    </nav>
  )
}

/**
 * Upcoming deadlines on goals that are still open.
 *
 * A goal with no `target_date` never appears here — "no deadline was set" is a
 * legitimate choice and rendering it as "0 days" would be a deadline NEXUS
 * invented. A target date already behind today is counted and named as such
 * rather than being called overdue, because the only fact on offer is that the
 * date is behind.
 */
function DeadlinesPanel({
  upcoming,
  pastCount,
  isLoading,
  isStale,
  error,
  onRetry,
}: {
  upcoming: readonly LearningGoalRead[]
  pastCount: number
  isLoading: boolean
  isStale: boolean
  error: ApiError | null
  onRetry: () => void
}) {
  return (
    <Card className="min-w-0">
      <CardHeader className="pb-4">
        <CardTitle level="h3">
          <span className="inline-flex items-center gap-2">
            <CalendarClock className="size-4" aria-hidden="true" />
            Upcoming deadlines
          </span>
        </CardTitle>
        <CardDescription>
          Goals that are still open and carry a target date, soonest first. A goal with no target
          date is not on this list, because it has no deadline to be approaching.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <LearningStaleNotice isStale={isStale} subject="the goals" />

        {error ? (
          <ErrorState error={error} title="The goals could not load" onRetry={onRetry} compact />
        ) : isLoading ? (
          <div role="status" aria-busy="true" className="space-y-2">
            <span className="sr-only">Loading the upcoming deadlines</span>
            {[0, 1].map((row) => (
              <Skeleton key={row} className="h-9 w-full" />
            ))}
          </div>
        ) : upcoming.length === 0 ? (
          <p className="text-sm leading-relaxed text-muted-foreground">
            No open goal carries a target date. A deadline is yours to set: add one to a goal and it
            appears here with the days remaining.
            {pastCount > 0
              ? ` ${pastCount} open ${pastCount === 1 ? 'goal has' : 'goals have'} a target date already past.`
              : ''}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {upcoming.map((goal) => (
              <li key={goal.id} className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                  {goal.title}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {describeTargetDate(goal.target_date)}
                </span>
              </li>
            ))}
          </ul>
        )}

        {!isLoading && !error && upcoming.length > 0 && pastCount > 0 && (
          <p className="text-xs text-muted-foreground">
            {pastCount} other open {pastCount === 1 ? 'goal has' : 'goals have'} a target date
            already past and {pastCount === 1 ? 'is' : 'are'} not listed above.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * The rule sweep: run the two deterministic learning rules and report what this
 * run raised.
 *
 * **The rules are threshold comparisons over the caller's own recorded figures —
 * there is no model behind them.** They watch for a goal whose deadline is
 * approaching while the recorded progress is low, and for a target skill with
 * nothing recorded against it beyond `career_stale_inactive_days`. Nothing is
 * scored, ranked or predicted, and the sweep is opt-in: it never runs on page
 * load, because a screen that quietly evaluates a person while they are looking
 * at their goals would be making the claim the rest of this page refuses to
 * make.
 *
 * **The backend's words, unedited.** A raised row's `title` and `reason` are
 * rendered verbatim, in the imperative the rule wrote, because the `reason` is
 * what carries the numbers that fired it — paraphrasing it is how a suggestion
 * drifts into "you are behind on X", which is a verdict about the person rather
 * than a fact about the records. Nothing on this panel says what the person
 * should think of the suggestion.
 *
 * **An empty result is a real answer and is never dressed up as a win.** The
 * endpoint returns only the suggestions *this call newly raised*, so `[]` means
 * every rule that currently fires was already on file. The panel says exactly
 * that and says that running it again refreshes those existing rows rather than
 * repeating them, so a second press is not read as a broken button.
 *
 * A failed call is the one path that reaches a toast: nothing was evaluated, and
 * a success message would be the one claim on this page the backend never made.
 */
function SuggestionsSweep() {
  const [raised, setRaised] = useState<RecommendationRead[] | null>(null)
  const evaluate = useEvaluateLearningRecommendations()
  const pending = evaluate.isPending

  const run = useCallback(async () => {
    // Cleared up front so a stale result is never sitting under a spinner that
    // is about to replace it.
    setRaised(null)
    try {
      const rows = await evaluate.mutateAsync()
      setRaised(rows)
    } catch (cause) {
      toast.error('The suggestions could not be checked', toApiError(cause).message)
    }
  }, [evaluate])

  return (
    <Card className="min-w-0">
      <CardHeader className="pb-4">
        <CardTitle level="h3">
          <span className="inline-flex items-center gap-2">
            <Lightbulb className="size-4" aria-hidden="true" />
            Suggestions
          </span>
        </CardTitle>
        <CardDescription>
          Two fixed rules run over the records on this page: a goal whose deadline is approaching
          while the progress you recorded for it is low, and a target skill with nothing recorded
          against it recently. They are threshold comparisons — nothing is scored, ranked or
          predicted — and each suggestion is a statement about a record, not a judgement about you.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        <Button type="button" variant="outline" disabled={pending} onClick={() => void run()}>
          {pending ? <Spinner size="sm" /> : <Play aria-hidden="true" />}
          {pending ? 'Checking your records…' : 'Check for suggestions'}
        </Button>

        <div role="status" aria-live="polite" className="space-y-2">
          {raised === null ? (
            <p className="text-sm leading-relaxed text-muted-foreground">
              Nothing has been checked yet. The rules run only when you ask them to, and only over
              what is already recorded here.
            </p>
          ) : raised.length === 0 ? (
            <p className="text-sm leading-relaxed text-muted-foreground">
              This run raised no new suggestions. Every rule that currently fires was already on
              file, so running it again refreshes those existing suggestions rather than repeating
              them — and it will not tell you anything new about your records until the records
              change.
            </p>
          ) : (
            <>
              <p className="text-sm leading-relaxed text-foreground">
                {raised.length === 1
                  ? 'This run raised 1 new suggestion.'
                  : `This run raised ${raised.length} new suggestions.`}{' '}
                Each one below is what a rule found in your records, in the rule's own words.
              </p>
              <ul className="space-y-2">
                {raised.map((suggestion) => (
                  <li
                    key={suggestion.id}
                    className="rounded-md border border-border bg-muted/40 p-3 text-sm"
                  >
                    <p className="font-medium text-foreground">{suggestion.title}</p>
                    <p className="mt-1 leading-relaxed text-muted-foreground">{suggestion.reason}</p>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">
                Only suggestions raised by this run are listed. Running it again refreshes these
                rather than adding them a second time.
              </p>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/* ---------------------------------------------------------------------- forms */

/**
 * Writes down a learning goal.
 *
 * **Every field here is the user's.** `progress` is not on the form at all,
 * because a percentage NEXUS set would be a claim about commitment rather than
 * about progress — the goal starts at the backend's own default and the user
 * moves it themselves. `target_skill_id` and `target_topic` are two forms of the
 * same idea, and the skill picker is offered with an explicit "no skill" option
 * so a subject can be named before the skill row exists.
 *
 * **A 422 is rendered under the field it names**, using the server's own
 * message, because the alternative — one banner at the top — leaves the reader
 * guessing which of six inputs was refused.
 */
function NewGoalDialog({
  open,
  onOpenChange,
  skills,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  skills: readonly SkillRead[]
}) {
  const [title, setTitle] = useState('')
  const [topic, setTopic] = useState('')
  const [skillId, setSkillId] = useState('')
  const [targetDate, setTargetDate] = useState('')
  const [priority, setPriority] = useState<ProjectPriority>('medium')
  const [effort, setEffort] = useState('')
  const [error, setError] = useState<ApiError | null>(null)

  const create = useCreateLearningGoal()
  const pending = create.isPending

  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function reset() {
    setTitle('')
    setTopic('')
    setSkillId('')
    setTargetDate('')
    setPriority('medium')
    setEffort('')
    setError(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    const name = title.trim()
    if (!name) {
      setError(toApiError(new Error('A goal needs a title — the thing you are working towards.')))
      return
    }

    const minutes = effort.trim() === '' ? null : Number(effort)
    if (minutes !== null && (!Number.isFinite(minutes) || minutes < 0)) {
      setError(toApiError(new Error('The effort estimate must be a number of minutes, or left empty.')))
      return
    }

    try {
      const saved = await create.mutateAsync({
        title: name,
        ...(topic.trim() ? { target_topic: topic.trim() } : {}),
        ...(skillId ? { target_skill_id: skillId } : {}),
        ...(targetDate ? { target_date: targetDate } : {}),
        priority,
        ...(minutes !== null ? { estimated_effort_minutes: minutes } : {}),
      })
      toast.success('Goal written down', `"${saved.title}" is now on your list.`)
      reset()
      onOpenChange(false)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error('Could not save that goal', apiError.message)
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
          <DialogTitle>Write down a goal</DialogTitle>
          <DialogDescription>
            A goal is a title and, optionally, the skill or subject it is for and a date you want it
            by. NEXUS never creates one for you, and the progress figure on it is the one you set.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <form className="app-form-stack" onSubmit={submit} noValidate>
            {banner && (
              <p role="alert" className="app-form-error">
                {banner.message}
              </p>
            )}

            <Field
              id="goal-title"
              label="Title"
              error={fieldErrors.title}
              hint="What you are working towards."
            >
              <Input
                id="goal-title"
                value={title}
                required
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.title) || undefined}
                onChange={(event) => {
                  setTitle(event.target.value)
                  setError(null)
                }}
              />
            </Field>

            <Field
              id="goal-topic"
              label="Topic"
              optional
              error={fieldErrors.target_topic}
              hint="Free text for a subject that has no skill row yet. A goal can name a skill, a topic, or both."
            >
              <Input
                id="goal-topic"
                value={topic}
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.target_topic) || undefined}
                onChange={(event) => setTopic(event.target.value)}
              />
            </Field>

            <Field
              id="goal-skill"
              label="Skill"
              optional
              error={fieldErrors.target_skill_id}
            >
              <Select
                id="goal-skill"
                value={skillId}
                aria-invalid={Boolean(fieldErrors.target_skill_id) || undefined}
                onChange={(event) => setSkillId(event.target.value)}
              >
                <option value="">Not linked to a tracked skill</option>
                {skills.map((skill) => (
                  <option key={skill.id} value={skill.id}>
                    {skill.name}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="goal-date"
              label="Target date"
              optional
              error={fieldErrors.target_date}
              hint="Leave empty for no deadline. NEXUS does not set one for you."
            >
              <Input
                id="goal-date"
                type="date"
                value={targetDate}
                aria-invalid={Boolean(fieldErrors.target_date) || undefined}
                onChange={(event) => setTargetDate(event.target.value)}
              />
            </Field>

            <Field id="goal-priority" label="Priority" error={fieldErrors.priority}>
              <Select
                id="goal-priority"
                value={priority}
                onChange={(event) => setPriority(event.target.value as ProjectPriority)}
              >
                {GOAL_PRIORITIES.map((option) => (
                  <option key={option} value={option}>
                    {GOAL_PRIORITY_META[option].label}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="goal-effort"
              label="Estimated effort"
              optional
              error={fieldErrors.estimated_effort_minutes}
              hint="Minutes, and your own estimate. Leave empty and none is recorded."
            >
              <Input
                id="goal-effort"
                type="number"
                min={0}
                value={effort}
                aria-invalid={Boolean(fieldErrors.estimated_effort_minutes) || undefined}
                onChange={(event) => setEffort(event.target.value)}
              />
            </Field>

            <DialogFooter>
              <Button type="button" variant="ghost" disabled={pending} onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={pending || !title.trim()}>
                {pending ? (
                  <>
                    <Spinner size="sm" />
                    Saving…
                  </>
                ) : (
                  <>
                    <Target aria-hidden="true" />
                    Save goal
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

/**
 * Records one learning activity.
 *
 * **The duration is optional and stays omitted when it is blank.** An activity
 * may be an event rather than a span — a resource was opened, a concept was
 * recorded — and sending `0` for "it was instantaneous" would claim a measured
 * zero-length session, which is the conflation the nullable field exists to
 * prevent.
 *
 * `source_type` is left unset: the entry came from the person typing it, and a
 * client that set it to a subsystem name would be claiming a derivation it did
 * not perform.
 */
function RecordActivityDialog({
  open,
  onOpenChange,
  skills,
  goals,
  defaultSkillId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  skills: readonly SkillRead[]
  goals: readonly LearningGoalRead[]
  defaultSkillId: string
}) {
  const [title, setTitle] = useState('')
  const [activityType, setActivityType] = useState<LearningActivityType>('study_session')
  const [skillId, setSkillId] = useState('')
  const [goalId, setGoalId] = useState('')
  const [duration, setDuration] = useState('')
  const [error, setError] = useState<ApiError | null>(null)

  const create = useCreateLearningActivity()
  const pending = create.isPending

  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function reset() {
    setTitle('')
    setActivityType('study_session')
    setSkillId(defaultSkillId)
    setGoalId('')
    setDuration('')
    setError(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    const name = title.trim()
    if (!name) {
      setError(toApiError(new Error('An activity needs a title describing what you did.')))
      return
    }

    const minutes = duration.trim() === '' ? null : Number(duration)
    if (minutes !== null && (!Number.isFinite(minutes) || minutes < 0)) {
      setError(toApiError(new Error('The duration must be a number of minutes, or left empty.')))
      return
    }

    try {
      await create.mutateAsync({
        title: name,
        activity_type: activityType,
        ...(skillId ? { skill_id: skillId } : {}),
        ...(goalId ? { goal_id: goalId } : {}),
        ...(minutes !== null ? { duration_minutes: minutes } : {}),
      })
      toast.success('Activity recorded', `"${name}" is now part of the trail.`)
      reset()
      onOpenChange(false)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error('Could not record that activity', apiError.message)
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
          <DialogTitle>Record a learning activity</DialogTitle>
          <DialogDescription>
            One recorded event. Nothing here implies that anything was understood — a resource that
            was opened is recorded as exactly that, and it stays a separate kind from a concept
            recorded.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <form className="app-form-stack" onSubmit={submit} noValidate>
            {banner && (
              <p role="alert" className="app-form-error">
                {banner.message}
              </p>
            )}

            <Field
              id="activity-title"
              label="What you did"
              error={fieldErrors.title}
              hint="A short description of the event you are recording."
            >
              <Input
                id="activity-title"
                value={title}
                required
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.title) || undefined}
                onChange={(event) => {
                  setTitle(event.target.value)
                  setError(null)
                }}
              />
            </Field>

            <Field id="activity-type" label="Kind of event" error={fieldErrors.activity_type}>
              <Select
                id="activity-type"
                value={activityType}
                onChange={(event) => setActivityType(event.target.value as LearningActivityType)}
              >
                {ACTIVITY_TYPE_ORDER.map((type) => (
                  <option key={type} value={type}>
                    {ACTIVITY_TYPE_META[type].label}
                  </option>
                ))}
              </Select>
            </Field>

            <Field id="activity-skill" label="Skill" optional error={fieldErrors.skill_id}>
              <Select
                id="activity-skill"
                value={skillId}
                onChange={(event) => setSkillId(event.target.value)}
              >
                <option value="">Not linked to a skill</option>
                {skills.map((skill) => (
                  <option key={skill.id} value={skill.id}>
                    {skill.name}
                  </option>
                ))}
              </Select>
            </Field>

            <Field id="activity-goal" label="Goal" optional error={fieldErrors.goal_id}>
              <Select
                id="activity-goal"
                value={goalId}
                onChange={(event) => setGoalId(event.target.value)}
              >
                <option value="">Not linked to a goal</option>
                {goals.map((goal) => (
                  <option key={goal.id} value={goal.id}>
                    {goal.title}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="activity-duration"
              label="Duration in minutes"
              optional
              error={fieldErrors.duration_minutes}
              hint="Leave empty when the entry is an event rather than a span — a page that was opened has no length."
            >
              <Input
                id="activity-duration"
                type="number"
                min={0}
                value={duration}
                aria-invalid={Boolean(fieldErrors.duration_minutes) || undefined}
                onChange={(event) => setDuration(event.target.value)}
              />
            </Field>

            <DialogFooter>
              <Button type="button" variant="ghost" disabled={pending} onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={pending || !title.trim()}>
                {pending ? (
                  <>
                    <Spinner size="sm" />
                    Recording…
                  </>
                ) : (
                  <>
                    <Plus aria-hidden="true" />
                    Record activity
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

/**
 * Adds a tracked skill at a level the user sets.
 *
 * **`level_source` is never sent.** It defaults to `user_defined` server-side,
 * so a skill created from this form is attributed to the person who filled it
 * in — which is the only honest attribution when no activity has been recorded
 * against it yet. Handing the client a `system_estimate` control would let
 * anyone relabel a claim as an inference.
 *
 * `confidence` and `evidence_count` are absent from the payload by design: they
 * are measured from recorded activity, and a client that could set them could
 * forge the number that lends an estimate its credibility.
 */
function AddSkillDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [name, setName] = useState('')
  const [category, setCategory] = useState('')
  const [currentLevel, setCurrentLevel] = useState('1')
  const [targetLevel, setTargetLevel] = useState('3')
  const [error, setError] = useState<ApiError | null>(null)

  const create = useCreateSkill()
  const pending = create.isPending

  const fieldErrors = fieldErrorMessages(error)
  const banner = bannerError(error)

  function reset() {
    setName('')
    setCategory('')
    setCurrentLevel('1')
    setTargetLevel('3')
    setError(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    const skillName = name.trim()
    if (!skillName) {
      setError(toApiError(new Error('A skill needs a name.')))
      return
    }

    const current = Number(currentLevel)
    const target = Number(targetLevel)
    if (!Number.isInteger(current) || current < 1 || current > 5) {
      setError(toApiError(new Error('The current level must be a whole number from 1 to 5.')))
      return
    }
    if (!Number.isInteger(target) || target < 1 || target > 5) {
      setError(toApiError(new Error('The target level must be a whole number from 1 to 5.')))
      return
    }

    try {
      await create.mutateAsync({
        name: skillName,
        ...(category.trim() ? { category: category.trim() } : {}),
        current_level: current,
        target_level: target,
      })
      toast.success('Skill tracked', `"${skillName}" is on your list at the level you set.`)
      reset()
      onOpenChange(false)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error('Could not add that skill', apiError.message)
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
          <DialogTitle>Add a skill</DialogTitle>
          <DialogDescription>
            A name and the level you claim for it. The level is stored as yours — NEXUS records it
            and does not dispute it — and it can only move to an estimate once enough activity has
            been recorded to justify one.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <form className="app-form-stack" onSubmit={submit} noValidate>
            {banner && (
              <p role="alert" className="app-form-error">
                {banner.message}
              </p>
            )}

            <Field id="skill-name" label="Name" error={fieldErrors.name}>
              <Input
                id="skill-name"
                value={name}
                required
                maxLength={120}
                aria-invalid={Boolean(fieldErrors.name) || undefined}
                onChange={(event) => {
                  setName(event.target.value)
                  setError(null)
                }}
              />
            </Field>

            <Field
              id="skill-category"
              label="Category"
              optional
              error={fieldErrors.category}
              hint="Free text. Language, framework, domain and practice are suggestions, not a closed list."
            >
              <Input
                id="skill-category"
                value={category}
                maxLength={64}
                aria-invalid={Boolean(fieldErrors.category) || undefined}
                onChange={(event) => setCategory(event.target.value)}
              />
            </Field>

            <Field
              id="skill-current-level"
              label="Current level (1–5)"
              error={fieldErrors.current_level}
              hint="Your own claim about where you are today."
            >
              <Select
                id="skill-current-level"
                value={currentLevel}
                onChange={(event) => setCurrentLevel(event.target.value)}
              >
                {[1, 2, 3, 4, 5].map((level) => (
                  <option key={level} value={String(level)}>
                    {level}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="skill-target-level"
              label="Target level (1–5)"
              error={fieldErrors.target_level}
              hint="Where you want to be. The gap between the two is computed on read, never stored."
            >
              <Select
                id="skill-target-level"
                value={targetLevel}
                onChange={(event) => setTargetLevel(event.target.value)}
              >
                {[1, 2, 3, 4, 5].map((level) => (
                  <option key={level} value={String(level)}>
                    {level}
                  </option>
                ))}
              </Select>
            </Field>

            <DialogFooter>
              <Button type="button" variant="ghost" disabled={pending} onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={pending || !name.trim()}>
                {pending ? (
                  <>
                    <Spinner size="sm" />
                    Saving…
                  </>
                ) : (
                  <>
                    <GraduationCap aria-hidden="true" />
                    Add skill
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

/**
 * One labelled form control, its hint, and the server's own message under it.
 *
 * **The description lands on the control itself.** `aria-describedby` on a
 * wrapper `<div>` reaches nothing — a screen reader announces the description
 * when focus enters the element that carries it — so the hint and error ids are
 * merged onto the child rather than onto the box around it. That is what makes
 * the refusal audible on the field that caused it instead of appearing as a
 * stray paragraph halfway down the form.
 */
function Field({
  id,
  label,
  children,
  error,
  hint,
  optional = false,
}: {
  id: string
  label: string
  children: ReactNode
  error?: string
  hint?: string
  optional?: boolean
}) {
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className="app-form-field">
      <Label htmlFor={id} optional={optional}>
        {label}
      </Label>
      {withDescribedBy(children, describedBy)}
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="app-form-error">
          {error}
        </p>
      )}
    </div>
  )
}

/** Merges the wrapper's ids into whatever single element the caller passed. */
function withDescribedBy(child: ReactNode, describedBy: string | undefined): ReactNode {
  if (!describedBy || !isValidElement(child)) return child
  const existing = (child.props as { 'aria-describedby'?: string })['aria-describedby']
  const merged = [existing, describedBy].filter(Boolean).join(' ')
  return cloneElement(child as ReactElement<{ 'aria-describedby'?: string }>, {
    'aria-describedby': merged,
  })
}