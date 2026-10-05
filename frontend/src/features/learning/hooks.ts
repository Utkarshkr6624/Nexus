/**
 * TanStack Query bindings for the Phase 9 learning and career surface.
 *
 * **`learningKeys` and `careerKeys` are the single owners of the query-key
 * shape.** Every key lives under its own `['learning']` or `['career']` root
 * and every *filtered or windowed* key carries its resolved parameters,
 * normalised to fixed length with `?? null` for each unset field. That is not a
 * style preference: a params object rebuilt on every render hashes to the same
 * key when unset fields become `null` and thrashes the cache when they are
 * dropped, so `useLearningSummary({ window_days: 30 })` with a fresh literal
 * does not refetch on every keystroke in the surrounding component.
 *
 * **Mutations invalidate whole subtrees, and two of them invalidate both.** A
 * recorded activity bumps the skill's `evidence_count` and `last_activity_at`,
 * which the gaps, the summary and the activity series all read — and it is also
 * counted by `career_features.learning_activity`, so a learning mutation that
 * records an activity clears the career tree as well. A skill write clears both
 * for the mirror-image reason: deleting one nulls the `skill_id` on career
 * evidence that pointed at it. Goal and profile writes only touch their own
 * tree, because nothing outside it reads them.
 *
 * **No level, role, employer or date is ever derived here.** These hooks move
 * rows the user typed; the one number a client could forge — a skill level with
 * a `level_source` that misdescribes it — is closed off by the payload types,
 * which carry `level_source` as the only attribution a level may have.
 *
 * **Ownership is the server's alone.** Every route filters on `user_id`, so
 * another account's goal, skill, activity, profile or evidence is a 404 and
 * never a 403; nothing in this file is told the difference and no id is ever
 * sent with a user id beside it.
 *
 * **The window lives in the URL**, via {@link useLearningWindow}, for the same
 * reason analytics and the developer surface keep it there: `?range=90d` is a
 * shareable view and the back button walks windows rather than leaving the page.
 *
 * **Retry policy is inherited.** `app/query-client.ts` refuses to retry a 4xx,
 * so the 422 from a window wider than `learning_max_window_days` and the 404 for
 * a missing profile both surface on the first response and are the page's to
 * explain.
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
  createCareerEvidence,
  createCareerExperience,
  createLearningActivity,
  createLearningGoal,
  createSkill,
  evaluateLearningRecommendations,
  fetchCareerEvidence,
  fetchCareerExperience,
  fetchCareerProfile,
  fetchCareerSummary,
  fetchLearningActivities,
  fetchLearningActivity,
  fetchLearningGoals,
  fetchLearningSummary,
  fetchSkillGaps,
  fetchSkills,
  upsertCareerProfile,
} from '@/services/learning'
import type { DateOnlyString, Granularity } from '@/types/analytics'
import { isDateOnly, rangeDays, shiftDays, todayDateOnly } from '@/types/analytics'
import {
  LEARNING_DEFAULT_WINDOW_DAYS,
  MAX_LEARNING_WINDOW_DAYS,
} from '@/types/learning'
import type {
  CareerEvidenceCreatePayload,
  CareerEvidenceListParams,
  CareerEvidenceListRead,
  CareerEvidenceRead,
  CareerExperienceCreatePayload,
  CareerExperienceListParams,
  CareerExperienceListRead,
  CareerExperienceRead,
  CareerProfileRead,
  CareerProfileUpsert,
  CareerSummaryRead,
  LearningActivityCreatePayload,
  LearningActivityListParams,
  LearningActivityListRead,
  LearningActivityRead,
  LearningActivitySeriesRead,
  LearningGoalCreatePayload,
  LearningGoalListParams,
  LearningGoalListRead,
  LearningGoalRead,
  LearningSummaryRead,
  LearningWindowParams,
  SkillCreatePayload,
  SkillGapListRead,
  SkillGapParams,
  SkillListParams,
  SkillListRead,
  SkillRead,
  UUIDString,
} from '@/types/learning'
import type { RecommendationRead } from '@/types/risk'

type Enabled = { enabled?: boolean }

/**
 * The activity series additionally carries a bucket grain in its key.
 *
 * The series is the only window-shaped read whose *shape* differs with the
 * grain, so only its key needs the extra slot — pinning every other read to
 * `['learning', null]` would put a field that cannot change the answer into
 * every cache entry.
 */
export interface LearningActivityParams extends LearningWindowParams {
  /**
   * How wide one bucket should be.
   *
   * Part of the key and of the URL, but **not yet of the request**: the service
   * wrapper takes a `LearningWindowParams`, so the server keeps bucketing by
   * its own grain and this value is a view preference the dashboard re-buckets
   * with. It is carried here rather than dropped so a link carries the choice it
   * was made with, and so the parameter starts taking effect the moment the
   * service forwards it — without a signature change on either side of this
   * file.
   */
  granularity?: Granularity
}

/* -------------------------------------------------------------- key helpers */

/**
 * Window-only key part. `window_days` is optional because the backend owns the
 * default, so an unset window is a real, distinct request — recorded as `null`,
 * not omitted, because dropping it would collide with an explicit `30`.
 */
function windowKeyPart(params: LearningWindowParams = {}): unknown[] {
  return [params.window_days ?? null]
}

/** The activity series is a function of its window and its grain. */
function activitySeriesKeyPart(params: LearningActivityParams = {}): unknown[] {
  return [params.window_days ?? null, params.granularity ?? null]
}

/** A gap is a function of its window and of the skill it was narrowed to. */
function gapKeyPart(params: SkillGapParams = {}): unknown[] {
  return [params.window_days ?? null, params.skill_id ?? null]
}

/**
 * The goal list: a page of a filtered set.
 *
 * `status` is served from `ix_learning_goals_owner_status` and the two
 * `target_date` bounds from `ix_learning_goals_owner_target_date`, so all three
 * are filters the backend answers without a scan — and a filtered list and an
 * unfiltered one are genuinely different answers rather than the same rows in a
 * different order.
 */
function goalKeyPart(params: LearningGoalListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.status ?? null,
    params.target_skill_id ?? null,
    params.project_id ?? null,
    params.target_before ?? null,
    params.target_after ?? null,
  ]
}

/** The skill list: a page of a filtered set. */
function skillKeyPart(params: SkillListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.category ?? null,
    params.active_since ?? null,
  ]
}

/** The activity trail: a page of a filtered set, newest first. */
function activityListKeyPart(params: LearningActivityListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.skill_id ?? null,
    params.goal_id ?? null,
    params.activity_type ?? null,
    params.window_days ?? null,
    params.occurred_from ?? null,
    params.occurred_to ?? null,
  ]
}

/** Dated records: a page of a filtered set, split by kind on the server. */
function experienceKeyPart(params: CareerExperienceListParams = {}): unknown[] {
  return [params.limit ?? null, params.offset ?? null, params.kind ?? null]
}

/** Evidence: a page of a filtered set, newest first. */
function evidenceKeyPart(params: CareerEvidenceListParams = {}): unknown[] {
  return [
    params.limit ?? null,
    params.offset ?? null,
    params.evidence_type ?? null,
    params.project_id ?? null,
    params.skill_id ?? null,
    params.occurred_from ?? null,
    params.occurred_to ?? null,
  ]
}

/** Stable key factory. Every learning key lives under the `['learning']` root. */
export const learningKeys = {
  all: () => ['learning'] as const,
  summary: (params: LearningWindowParams = {}) =>
    ['learning', 'summary', ...windowKeyPart(params)] as const,
  metrics: (params: LearningWindowParams = {}) =>
    ['learning', 'metrics', ...windowKeyPart(params)] as const,
  gaps: (params: SkillGapParams = {}) => ['learning', 'gaps', ...gapKeyPart(params)] as const,
  activity: (params: LearningActivityParams = {}) =>
    ['learning', 'activity', ...activitySeriesKeyPart(params)] as const,
  features: (params: LearningWindowParams = {}) =>
    ['learning', 'features', ...windowKeyPart(params)] as const,
  goals: (params: LearningGoalListParams = {}) =>
    ['learning', 'goals', ...goalKeyPart(params)] as const,
  goal: (id: UUIDString) => ['learning', 'goal', id] as const,
  skills: (params: SkillListParams = {}) =>
    ['learning', 'skills', ...skillKeyPart(params)] as const,
  skill: (id: UUIDString) => ['learning', 'skill', id] as const,
  activities: (params: LearningActivityListParams = {}) =>
    ['learning', 'activities', ...activityListKeyPart(params)] as const,
}

/**
 * Stable key factory for the career tree.
 *
 * Separate from {@link learningKeys} because the two routers have separate
 * prefixes and separate permissions checks — sharing a root would let a career
 * invalidation refetch the learning dashboard and the reverse.
 *
 * `profile` carries no key parts: there is exactly one profile per account and
 * the caller does not name it. `summary` and `features` take no parameters
 * either, because neither endpoint accepts any.
 */
export const careerKeys = {
  all: () => ['career'] as const,
  summary: () => ['career', 'summary'] as const,
  profile: () => ['career', 'profile'] as const,
  features: () => ['career', 'features'] as const,
  experience: (params: CareerExperienceListParams = {}) =>
    ['career', 'experience', ...experienceKeyPart(params)] as const,
  experienceItem: (id: UUIDString) => ['career', 'experience', id] as const,
  evidence: (params: CareerEvidenceListParams = {}) =>
    ['career', 'evidence', ...evidenceKeyPart(params)] as const,
  evidenceItem: (id: UUIDString) => ['career', 'evidence', id] as const,
}

/* ------------------------------------------------------- window (in the URL) */

/**
 * The window shortcuts the learning picker offers.
 *
 * A learning window is a *trailing span*, not a pair of dates: the endpoints
 * accept `window_days` and resolve it backwards from today server-side. There is
 * therefore no one-day preset — analytics' `today` is the `1d` case here and it
 * is left to Custom — and the widest preset matches
 * {@link MAX_LEARNING_WINDOW_DAYS} because anything wider is a 422.
 *
 * Learning-specific on purpose rather than reusing the analytics or developer
 * presets: `30d` here means "the server's own `learning_default_window_days`",
 * and pinning it client-side would make a link mean something different from the
 * account whose configuration changed.
 */
export type LearningWindowPresetId = '7d' | '30d' | '90d' | '180d' | '365d' | 'custom'

export interface LearningWindowPreset {
  id: LearningWindowPresetId
  label: string
  /** Undefined for `custom`, whose length comes from `?start`/`?end`. */
  days?: number
}

export const LEARNING_WINDOW_PRESETS: readonly LearningWindowPreset[] = [
  { id: '7d', label: '7 days', days: 7 },
  { id: '30d', label: '30 days', days: 30 },
  { id: '90d', label: '90 days', days: 90 },
  { id: '180d', label: '180 days', days: 180 },
  { id: '365d', label: '365 days', days: 365 },
  { id: 'custom', label: 'Custom' },
]

/**
 * The default preset, mirroring `learning_default_window_days`.
 *
 * Choosing the default means sending **no** `window_days` at all, so the backend
 * applies its own configured value. A client that pinned 30 would quietly ignore
 * an account whose default had been changed.
 */
export const DEFAULT_LEARNING_WINDOW_PRESET: LearningWindowPresetId = '30d'

/** The bucket grain a link carries when it says nothing about one. */
export const DEFAULT_LEARNING_GRANULARITY: Granularity = 'day'

const GRANULARITIES: readonly string[] = ['day', 'week', 'month']

/** Whether a `?range=` value names a preset this surface offers. */
export function isLearningWindowPreset(
  value: string | null | undefined,
): value is LearningWindowPresetId {
  return value !== null && LEARNING_WINDOW_PRESETS.some((preset) => preset.id === value)
}

/** Clamps a span into what the endpoints will accept. Zero is not a window. */
function clampWindowDays(days: number): number {
  return Math.min(Math.max(Math.round(days), 1), MAX_LEARNING_WINDOW_DAYS)
}

export interface LearningWindow {
  preset: LearningWindowPresetId
  /**
   * The trailing span in days, or `undefined` for the default preset.
   *
   * `undefined` means "ask the backend for its own default" — not "zero days"
   * and not "the default is 30". `LearningSummaryRead.window_days` echoes what
   * the server actually used, so a caption quotes that rather than this.
   */
  window_days: number | undefined
  granularity: Granularity
  /**
   * The custom range, always resolved — `?start`/`?end` when present, otherwise
   * the last {@link LEARNING_DEFAULT_WINDOW_DAYS} days — so a picker can render
   * its inputs before the user has chosen one, and switching back to Custom
   * restores what they chose rather than resetting it.
   */
  custom: { start: DateOnlyString; end: DateOnlyString } | null
  /** Ready-made request parameters for the four window-shaped reads. */
  params: LearningWindowParams
  /** The same, plus the bucket grain, for the activity series. */
  activityParams: LearningActivityParams
  setPreset: (preset: LearningWindowPresetId) => void
  setCustom: (start: DateOnlyString, end: DateOnlyString) => void
  setGranularity: (granularity: Granularity) => void
}

/**
 * The learning window, held in the URL.
 *
 * **Shareable, not remembered.** `?range=90d` means the same thing to whoever
 * receives the link, and the browser's back button steps through windows instead
 * of out of the page. A param equal to its default is *deleted* rather than
 * written, so the common case stays a clean URL and two people looking at the
 * same view produce the same string.
 *
 * **A custom window contributes its length, not its position.** The endpoints
 * resolve `window_days` backwards from today, so
 * `?start=2026-01-01&end=2026-01-31` is sent as `window_days: 31`. The range is
 * retained in `custom` so the picker can show what was chosen and restore it; it
 * is not sent, because there is no parameter to send it in and inventing one
 * would silently do nothing.
 *
 * **`params` and `activityParams` are memoised** and contain only normalised
 * fields, so they can be spread straight into a query hook without changing the
 * hash of the key.
 */
export function useLearningWindow(
  defaultPreset: LearningWindowPresetId = DEFAULT_LEARNING_WINDOW_PRESET,
): LearningWindow {
  const [searchParams, setSearchParams] = useSearchParams()

  const rangeParam = searchParams.get('range')
  const preset: LearningWindowPresetId = isLearningWindowPreset(rangeParam)
    ? rangeParam
    : defaultPreset

  const startParam = searchParams.get('start')
  const endParam = searchParams.get('end')

  const custom = useMemo(() => {
    const today = todayDateOnly()
    const start = isDateOnly(startParam)
      ? startParam
      : shiftDays(today, -(LEARNING_DEFAULT_WINDOW_DAYS - 1))
    const end = isDateOnly(endParam) ? endParam : today
    // An inverted custom window is a 422 server-side; clamp rather than render
    // a screen that can only ever show an error.
    return start <= end ? { start, end } : { start: end, end: start }
  }, [startParam, endParam])

  const granularityParam = searchParams.get('granularity')
  const granularity: Granularity = GRANULARITIES.includes(granularityParam ?? '')
    ? (granularityParam as Granularity)
    : DEFAULT_LEARNING_GRANULARITY

  const windowDays = useMemo<number | undefined>(() => {
    // The default preset sends nothing at all and lets the backend decide.
    if (preset === defaultPreset) return undefined
    if (preset === 'custom') {
      const days = rangeDays(custom.start, custom.end)
      return days > 0 ? clampWindowDays(days) : undefined
    }
    const days = LEARNING_WINDOW_PRESETS.find((entry) => entry.id === preset)?.days
    return days === undefined ? undefined : clampWindowDays(days)
  }, [preset, custom, defaultPreset])

  const params = useMemo<LearningWindowParams>(() => ({ window_days: windowDays }), [windowDays])
  const activityParams = useMemo<LearningActivityParams>(
    () => ({ window_days: windowDays, granularity }),
    [windowDays, granularity],
  )

  const write = useCallback(
    (next: {
      preset: LearningWindowPresetId
      start?: DateOnlyString
      end?: DateOnlyString
      granularity?: Granularity
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
      if (next.granularity && next.granularity !== DEFAULT_LEARNING_GRANULARITY) {
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
    activityParams,
    // The grain is carried through every setter, so changing the range does not
    // silently re-bucket the chart: switching to 90 days should not also undo a
    // week grain the user had already chosen. Only `setGranularity` changes it.
    setPreset: (next) => write({ preset: next, granularity }),
    setCustom: (start, end) => write({ preset: 'custom', start, end, granularity }),
    setGranularity: (next) => write({ preset, granularity: next }),
  }
}

/* ------------------------------------------------------- learning: dashboard */

/**
 * The dashboard's single request: counts, the sentence describing them and the
 * window they were computed over.
 *
 * One round trip, so the header tiles and the sentence beneath them cannot quote
 * different totals. An account with nothing recorded answers with real zeroes
 * *and* `has_data: false` — the counts are honest, and the flag is what turns
 * them into an empty state to explain rather than a finding about the person.
 *
 * There is no average level and no strongest skill in the response, and this
 * hook adds neither: a level nobody can attribute is not a level.
 *
 * `placeholderData` keeps the previous window on screen while the next one
 * loads; a query reading `isPlaceholderData` can say the figures are the
 * previous window's.
 */
export function useLearningSummary(
  params: LearningWindowParams = {},
  options: Enabled = {},
): UseQueryResult<LearningSummaryRead> {
  return useQuery({
    queryKey: learningKeys.summary(params),
    queryFn: ({ signal }) => fetchLearningSummary(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * Each skill's distance from its target level, with the evidence behind it.
 *
 * **Computed on the server on every read and never stored**, so the same skill
 * with one more recorded activity is a different gap with a different
 * explanation. `available: false` rows are present in the list rather than
 * omitted, because "nothing has been recorded for this skill" is the honest
 * answer on a cold account and the page must be able to say it instead of
 * drawing an empty chart.
 *
 * **The whole envelope comes back**, `items` plus `total`,
 * `available_count`, `unavailable_count` and `by_level_source`. A consumer that
 * wants rows destructures `data?.items`; one that also wants the tally reads it
 * off the same object rather than counting the page in hand, which would be a
 * different and wrong number.
 *
 * `skill_id` is part of the key because "every skill" and "this skill" are
 * different answers and must never share a cache entry.
 */
export function useSkillGaps(
  params: SkillGapParams = {},
  options: Enabled = {},
): UseQueryResult<SkillGapListRead> {
  return useQuery({
    queryKey: learningKeys.gaps(params),
    queryFn: ({ signal }) => fetchSkillGaps(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * The zero-filled activity series over the window.
 *
 * The buckets are dense on the wire — a quiet day arrives with a count of zero
 * rather than being skipped — so a chart can plot them as they are without
 * re-implementing the fill. The grain is in the key because it changes the
 * series' shape; see {@link LearningActivityParams.granularity} for why it is
 * not yet in the request.
 */
export function useLearningActivity(
  params: LearningActivityParams = {},
  options: Enabled = {},
): UseQueryResult<LearningActivitySeriesRead> {
  return useQuery({
    queryKey: learningKeys.activity(params),
    queryFn: ({ signal }) => fetchLearningActivity(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/* ---------------------------------------------------------- learning: goals */

/**
 * Learning goals, filtered and paginated.
 *
 * `total` describes the filtered set rather than the length of this page, so a
 * header cannot quote a number the pager contradicts.
 */
export function useLearningGoals(
  params: LearningGoalListParams = {},
  options: Enabled = {},
): UseQueryResult<LearningGoalListRead> {
  return useQuery({
    queryKey: learningKeys.goals(params),
    queryFn: ({ signal }) => fetchLearningGoals(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/* --------------------------------------------------------- learning: skills */

/** Tracked skills, filtered and paginated. */
export function useSkills(
  params: SkillListParams = {},
  options: Enabled = {},
): UseQueryResult<SkillListRead> {
  return useQuery({
    queryKey: learningKeys.skills(params),
    queryFn: ({ signal }) => fetchSkills(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/* ------------------------------------------------------ learning: activities */

/** The recorded trail, newest first, filtered and paginated. */
export function useLearningActivities(
  params: LearningActivityListParams = {},
  options: Enabled = {},
): UseQueryResult<LearningActivityListRead> {
  return useQuery({
    queryKey: learningKeys.activities(params),
    queryFn: ({ signal }) => fetchLearningActivities(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/* ----------------------------------------------------------- career: reads */

/**
 * The career dashboard's headline figures.
 *
 * Counts only, with `has_profile` separate from the counts so the page can tell
 * "no profile yet" from "a profile with nothing on it yet". There is no readiness
 * score and no employer match in the response, because a score would be a verdict
 * derived from records that do not support one.
 */
export function useCareerSummary(options: Enabled = {}): UseQueryResult<CareerSummaryRead> {
  return useQuery({
    queryKey: careerKeys.summary(),
    queryFn: ({ signal }) => fetchCareerSummary(signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * The caller's career profile, or `null` when there is not one yet.
 *
 * **A missing profile is a `null` payload, not a failure**: the endpoint answers
 * `CareerProfileRead | null`, so `data` is `null` on an account that has never
 * had a profile and `error` stays null. `enabled` is left on so the page can
 * distinguish "not loaded yet" from "loaded, and there is nothing there"; the
 * upsert below is what creates the row.
 */
export function useCareerProfile(
  options: Enabled = {},
): UseQueryResult<CareerProfileRead | null> {
  return useQuery({
    queryKey: careerKeys.profile(),
    queryFn: ({ signal }) => fetchCareerProfile(signal),
    enabled: options.enabled,
  })
}

/** Education, work experience and certification records, filtered and paged. */
export function useCareerExperience(
  params: CareerExperienceListParams = {},
  options: Enabled = {},
): UseQueryResult<CareerExperienceListRead> {
  return useQuery({
    queryKey: careerKeys.experience(params),
    queryFn: ({ signal }) => fetchCareerExperience(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/** Career evidence, newest first, filtered and paginated. */
export function useCareerEvidence(
  params: CareerEvidenceListParams = {},
  options: Enabled = {},
): UseQueryResult<CareerEvidenceListRead> {
  return useQuery({
    queryKey: careerKeys.evidence(params),
    queryFn: ({ signal }) => fetchCareerEvidence(params, signal),
    enabled: options.enabled,
    placeholderData: (previous) => previous,
  })
}

/* ------------------------------------------------------ invalidation helpers */

/**
 * Clears the learning tree.
 *
 * Everything on the learning page is derived from goals, skills and activities,
 * and this account's own rows are the only ones in it, so the aggregate is the
 * correct trade rather than a guess at which keys a write "should" affect.
 */
function useInvalidateLearning() {
  const queryClient = useQueryClient()
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: learningKeys.all() })
  }, [queryClient])
}

/** Clears the career tree: summary, profile, experience, evidence and features. */
function useInvalidateCareer() {
  const queryClient = useQueryClient()
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: careerKeys.all() })
  }, [queryClient])
}

/**
 * Clears both trees.
 *
 * Two writes genuinely cross the boundary and are handled by this rather than by
 * two calls at the call site: recording an activity changes
 * `career_features.learning_activity`, and writing a skill changes both the gaps
 * beside its level and the `skill_id` on any career evidence pointing at it.
 */
function useInvalidateBoth() {
  const invalidateLearning = useInvalidateLearning()
  const invalidateCareer = useInvalidateCareer()
  return useCallback(() => {
    invalidateLearning()
    invalidateCareer()
  }, [invalidateLearning, invalidateCareer])
}

/* -------------------------------------------------------- learning: writes */

/**
 * Records a learning goal.
 *
 * Everything except `title` is the user's own claim and defaults server-side;
 * `target_skill_id` and `target_topic` are alternative forms of the same idea, so
 * a goal can name a subject before the skill exists and neither is required.
 */
export function useCreateLearningGoal(): UseMutationResult<
  LearningGoalRead,
  Error,
  LearningGoalCreatePayload
> {
  const invalidateLearning = useInvalidateLearning()
  return useMutation({
    mutationFn: (payload: LearningGoalCreatePayload) => createLearningGoal(payload),
    onSuccess: invalidateLearning,
  })
}

/* --------------------------------------------------------- learning: skills */

/**
 * Creates a skill at a level the user set.
 *
 * `level_source` defaults to `user_defined` server-side, so a skill created from
 * a form is attributed to the person who filled it in. The payload carries no
 * `confidence` and no `evidence_count`: those are measured from recorded
 * activity, and a client able to set them could forge the number that lends an
 * estimate its credibility.
 *
 * Both trees are cleared: a new skill joins the gap list and the counts.
 */
export function useCreateSkill(): UseMutationResult<SkillRead, Error, SkillCreatePayload> {
  const invalidateBoth = useInvalidateBoth()
  return useMutation({
    mutationFn: (payload: SkillCreatePayload) => createSkill(payload),
    onSuccess: invalidateBoth,
  })
}

/* ----------------------------------------------------- learning: activities */

/**
 * Records one learning activity, with where it came from.
 *
 * Real side effects beyond the row: the backend bumps the skill's
 * `evidence_count` and `last_activity_at` and emits
 * `LEARNING_SESSION_RECORDED`. Callers re-read the skill afterwards rather than
 * patching it optimistically — the bumped counters are the backend's numbers.
 *
 * Both trees are cleared because `career_features.learning_activity` counts
 * these rows.
 *
 * `duration_minutes` is omitted for an event rather than a span: sending `0` for
 * "it was instantaneous" would claim a measured zero-length session, which is
 * the same conflation the nullable field exists to prevent.
 */
export function useCreateLearningActivity(): UseMutationResult<
  LearningActivityRead,
  Error,
  LearningActivityCreatePayload
> {
  const invalidateBoth = useInvalidateBoth()
  return useMutation({
    mutationFn: (payload: LearningActivityCreatePayload) => createLearningActivity(payload),
    onSuccess: invalidateBoth,
  })
}

/* ----------------------------------------------- learning: the rule sweep */

/**
 * Runs the two deterministic learning rules and returns what *this call* newly
 * raised.
 *
 * A `POST` because it writes: a raised suggestion is a row, and re-running a rule
 * refreshes the row it already raised rather than adding a second one. That is
 * also why an empty array is a real answer and not a failure — it means every
 * suggestion that currently fires was already on file, so a caller may say "none
 * new" without the backend having to invent a filler row.
 *
 * **The rules are threshold comparisons over the caller's own recorded figures.**
 * There is no model behind this endpoint and none is planned: nothing here is
 * scored, ranked or predicted, and a returned row is a statement about the
 * records rather than a verdict about the person. Its `title` and `reason` are
 * the backend's own words, and the page shows them verbatim rather than
 * paraphrasing a sentence that carries the evidence.
 *
 * Only the learning tree is cleared. The sweep writes no career record and reads
 * no `career_features` column, so clearing career here would refetch a surface
 * this call cannot have changed.
 */
export function useEvaluateLearningRecommendations(): UseMutationResult<
  RecommendationRead[],
  Error,
  void
> {
  const invalidateLearning = useInvalidateLearning()
  return useMutation({
    mutationFn: () => evaluateLearningRecommendations(),
    onSuccess: invalidateLearning,
  })
}

/* ----------------------------------------------------------- career: writes */

/**
 * Creates or replaces the caller's career profile.
 *
 * A `PUT` keyed on the account's unique `user_id`, so sending the same body
 * twice leaves the same profile rather than a conflict or a second row.
 *
 * **Every field is the user's.** There is no generated summary, no inferred
 * target role and no highlight NEXUS chose; a form may prefill from the profile
 * it just fetched, but what it sends is still what the person wrote. The success
 * path also drops the 404 the read raised, so the page moves from "no profile"
 * to the profile without a reload.
 */
export function useUpsertCareerProfile(): UseMutationResult<
  CareerProfileRead,
  Error,
  CareerProfileUpsert
> {
  const invalidateCareer = useInvalidateCareer()
  return useMutation({
    mutationFn: (payload: CareerProfileUpsert) => upsertCareerProfile(payload),
    onSuccess: invalidateCareer,
  })
}

/**
 * Adds one dated record to the profile.
 *
 * `kind` and `title` are required; the organisation, the dates and the URL are
 * the user's to supply and are never inferred. A certification with no issuer
 * and no date is a valid row that says only what the user said.
 */
export function useCreateCareerExperience(): UseMutationResult<
  CareerExperienceRead,
  Error,
  CareerExperienceCreatePayload
> {
  const invalidateCareer = useInvalidateCareer()
  return useMutation({
    mutationFn: (payload: CareerExperienceCreatePayload) => createCareerExperience(payload),
    onSuccess: invalidateCareer,
  })
}

/**
 * Adds one piece of career evidence.
 *
 * `evidence_type`, `title` and `occurred_on` are required, because evidence
 * with no date could not be ordered. `source` defaults to `manual`, which is the
 * correct value for anything the user typed and the only one a client should
 * send by hand: claiming a subsystem derivation would be the same forgery as
 * inventing the qualification itself.
 */
export function useCreateCareerEvidence(): UseMutationResult<
  CareerEvidenceRead,
  Error,
  CareerEvidenceCreatePayload
> {
  const invalidateCareer = useInvalidateCareer()
  return useMutation({
    mutationFn: (payload: CareerEvidenceCreatePayload) => createCareerEvidence(payload),
    onSuccess: invalidateCareer,
  })
}

