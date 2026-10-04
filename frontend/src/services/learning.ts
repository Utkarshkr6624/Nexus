/**
 * Thin typed wrappers over the Phase 9 learning and career endpoints.
 *
 * No React here — every function is a promise-returning call the hooks in
 * `features/learning/hooks.ts` and `features/career/hooks.ts` wrap in a
 * `queryFn`/`mutationFn`.
 *
 * Six decisions are dictated by the backend rather than chosen here, and each
 * exists because the alternative would let a client build a request the server
 * will refuse or misread:
 *
 * - **Literal sub-paths are declared before `/{id}`** on both routers, and
 *   `LEARNING_ENDPOINTS` and `CAREER_ENDPOINTS` keep the same shape:
 *   `/learning/summary`, `/metrics`, `/gaps`, `/activity` and `/features` are
 *   plain strings, and only paths that genuinely carry an id are arrow
 *   functions. One map, one ordering, and the id-bearing keys are unambiguous by
 *   their own type.
 * - **Every read is `(params = {}, signal?: AbortSignal)`.** `params` defaults
 *   to `{}` because an omitted `window_days` is not the same request as
 *   `window_days: 30`: the first asks the backend for its own configured
 *   default, and the second pins a range that may not match what the server
 *   would have chosen. `queryFrom` therefore drops `undefined`, `null` and `''`
 *   rather than serialising them, and skips arrays outright — `QueryParams`
 *   carries one scalar per key and its serialiser emits `?key=value`, never a
 *   repeated key, so an array would be dropped silently and the screen would
 *   show a wider result set than the user asked for.
 * - **Recording an activity and completing a goal are `POST`s.** They write
 *   rows, move a skill's `evidence_count`/`last_activity_at`, and emit
 *   `LEARNING_SESSION_RECORDED` and `LEARNING_GOAL_COMPLETED`, so their results
 *   must never be cached as though they were reads.
 * - **`PUT /career/profile` upserts** on the unique `user_id`, which is what
 *   makes it the one endpoint here with no id: there is exactly one profile per
 *   account and the caller does not name it. A `POST` would have needed a
 *   second uniqueness check to stay a single profile, which is the same problem
 *   one column longer.
 * - **`PATCH` never carries the measured fields.** The skill update payload has
 *   no `evidence_count` and no `confidence`,
 *   `CareerEvidenceCreatePayload` defaults `source` to `manual` and no client
 *   should send anything else, and the goal update payload has no
 *   `completed_at` — completion is a route, not an editable field. Each of
 *   those would otherwise be a way for a client to forge the one number that
 *   lends a claim its credibility.
 * - **No function here supplies a level, a target role, a certification, an
 *   employer or a date.** Every career field is passed through from the user.
 *   There is deliberately no convenience wrapper that derives one.
 *
 * Ownership is the server's alone: every route filters on `user_id`, so another
 * account's goal, skill, activity, profile or evidence is a 404 and never a
 * 403, and this module has no notion of whose rows it is asking for.
 *
 * Failures are not handled here. `apiClient` throws `ApiError` with the status,
 * the machine-readable code and any field-level details, and the hooks decide
 * what a 404 means for the screen.
 */
import { apiClient, queryFrom } from '@/lib/api-client'
import type { RecommendationRead } from '@/types/risk'
import type { ActivityGranularity } from '@/types/learning'
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
  SkillGapParams,
  SkillGapRead,
  SkillListParams,
  SkillListRead,
  SkillRead,
  UUIDString,
} from '@/types/learning'

export const LEARNING_ENDPOINTS = {
  summary: '/learning/summary',
  metrics: '/learning/metrics',
  gaps: '/learning/gaps',
  activity: '/learning/activity',
  features: '/learning/features',
  goals: '/learning/goals',
  goal: (id: UUIDString) => `/learning/goals/${id}`,
  goalComplete: (id: UUIDString) => `/learning/goals/${id}/complete`,
  skills: '/learning/skills',
  skill: (id: UUIDString) => `/learning/skills/${id}`,
  activities: '/learning/activities',
  recommendations: '/learning/recommendations',
} as const

export const CAREER_ENDPOINTS = {
  summary: '/career/summary',
  // Named in contract §6 alongside `/learning/features` rather than in the §5
  // route table, and declared first so it keeps matching before `/{id}` paths.
  features: '/career/features',
  profile: '/career/profile',
  experience: '/career/experience',
  experienceItem: (id: UUIDString) => `/career/experience/${id}`,
  evidence: '/career/evidence',
  evidenceItem: (id: UUIDString) => `/career/evidence/${id}`,
} as const

/**
 * Builds a query object, dropping anything unset.
 *
 * Typed as `object` rather than `Record<string, unknown>` because an interface
 * carries no implicit index signature and would not be assignable to that record
 * — the same reason `services/developer.ts` does it this way.
 */
/* ------------------------------------------------------------------ dashboard */

/**
 * The account-wide headline figures, plus the window they were computed over.
 *
 * A single round trip for the whole overview: the counts and the sentence
 * describing them come from one response, so the header and the tiles beneath it
 * cannot quote different totals. Returns all-zero counts with `has_data: false`
 * on an account with nothing recorded — an empty state to explain, not an error,
 * and the reason the flag exists.
 *
 * Note what the counts are: goals recorded, skills tracked, activities logged,
 * minutes recorded. There is no average level and no strongest skill, because
 * either would be an unattributed claim about the user.
 */
export function fetchLearningSummary(
  params: LearningWindowParams = {},
  signal?: AbortSignal,
): Promise<LearningSummaryRead> {
  return apiClient.get<LearningSummaryRead>(LEARNING_ENDPOINTS.summary, {
    query: queryFrom({ window_days: params.window_days }),
    signal,
  })
}

/**
 * The gap between each skill's current level and its target, computed on read.
 *
 * Nothing is stored, so this is recomputed on every request and must not be
 * cached as if it were a fact about a moment: the same skill with one more
 * recorded activity is a different gap with a different explanation.
 *
 * `available: false` rows are present in the list rather than omitted, because
 * "nothing has been recorded for this skill" is the honest answer on a cold
 * account and the page must be able to say it instead of drawing an empty chart.
 */
export function fetchSkillGaps(
  params: SkillGapParams = {},
  signal?: AbortSignal,
): Promise<SkillGapRead[]> {
  return apiClient.get<SkillGapRead[]>(LEARNING_ENDPOINTS.gaps, {
    query: queryFrom({ window_days: params.window_days, skill_id: params.skill_id }),
    signal,
  })
}

/**
 * The activity series over the window, with its buckets zero-filled.
 *
 * `by_type` is what stops the chart from presenting `resource_viewed` — a page
 * was opened — as equivalent to `concept_learned`. It is a count of recorded
 * events in every case, never a count of hours or of understanding.
 */
export function fetchLearningActivity(
  params: LearningWindowParams & { granularity?: ActivityGranularity } = {},
  signal?: AbortSignal,
): Promise<LearningActivitySeriesRead> {
  return apiClient.get<LearningActivitySeriesRead>(LEARNING_ENDPOINTS.activity, {
    query: queryFrom({
      window_days: params.window_days,
      granularity: params.granularity,
    }),
    signal,
  })
}

/* ---------------------------------------------------------------------- goals */

/** Learning goals, filtered and paginated. */
export function fetchLearningGoals(
  params: LearningGoalListParams = {},
  signal?: AbortSignal,
): Promise<LearningGoalListRead> {
  return apiClient.get<LearningGoalListRead>(LEARNING_ENDPOINTS.goals, {
    query: queryFrom({
      limit: params.limit,
      offset: params.offset,
      status: params.status,
      target_skill_id: params.target_skill_id,
      project_id: params.project_id,
      target_before: params.target_before,
      target_after: params.target_after,
    }),
    signal,
  })
}

/**
 * Records a new learning goal.
 *
 * A mutation: it writes a row and emits `LEARNING_GOAL_CREATED`, so its result
 * must never be cached as though it were a read. Everything except `title` is
 * the user's own claim and defaults server-side; `target_skill_id` and
 * `target_topic` are alternative forms of the same idea, so neither is required.
 */
export function createLearningGoal(
  payload: LearningGoalCreatePayload,
): Promise<LearningGoalRead> {
  return apiClient.post<LearningGoalRead>(LEARNING_ENDPOINTS.goals, payload)
}

/* --------------------------------------------------------------------- skills */

/** Skills, filtered and paginated. */
export function fetchSkills(
  params: SkillListParams = {},
  signal?: AbortSignal,
): Promise<SkillListRead> {
  return apiClient.get<SkillListRead>(LEARNING_ENDPOINTS.skills, {
    query: queryFrom({
      limit: params.limit,
      offset: params.offset,
      category: params.category,
      active_since: params.active_since,
    }),
    signal,
  })
}

/**
 * Creates a skill at a level the user set.
 *
 * `level_source` defaults to `user_defined` server-side, so a skill created from
 * a form is attributed to the person who filled it in. The payload has no
 * `confidence` and no `evidence_count`: those are measured from recorded
 * activity, and a client able to set them could forge the number that lends an
 * estimate its credibility.
 */
export function createSkill(payload: SkillCreatePayload): Promise<SkillRead> {
  return apiClient.post<SkillRead>(LEARNING_ENDPOINTS.skills, payload)
}

/* ----------------------------------------------------------------- activities */

/** Recorded learning activities, newest first, filtered and paginated. */
export function fetchLearningActivities(
  params: LearningActivityListParams = {},
  signal?: AbortSignal,
): Promise<LearningActivityListRead> {
  return apiClient.get<LearningActivityListRead>(LEARNING_ENDPOINTS.activities, {
    query: queryFrom({
      limit: params.limit,
      offset: params.offset,
      skill_id: params.skill_id,
      goal_id: params.goal_id,
      activity_type: params.activity_type,
      window_days: params.window_days,
      occurred_from: params.occurred_from,
      occurred_to: params.occurred_to,
    }),
    signal,
  })
}

/**
 * Records one learning activity, with where it came from.
 *
 * A mutation with real side effects beyond the row: the backend bumps the
 * skill's `evidence_count` and `last_activity_at` and emits
 * `LEARNING_SESSION_RECORDED`. Callers re-read the skill afterwards rather than
 * patching it optimistically — the bumped counters are the backend's numbers,
 * not a derived guess.
 *
 * `source_type`/`source_id` are the traceable half of the evidence rule: an
 * activity the user typed in leaves both null, and anything derived names the
 * record it came from. Nothing in this call claims the activity *completed*
 * anything; `activity_type` names the kind of event recorded, which is the whole
 * claim.
 */
export function createLearningActivity(
  payload: LearningActivityCreatePayload,
): Promise<LearningActivityRead> {
  return apiClient.post<LearningActivityRead>(LEARNING_ENDPOINTS.activities, payload)
}

/* --------------------------------------------------------------------- career */

/**
 * The career dashboard's headline figures.
 *
 * Counts only, with `has_profile` separate from the counts so the page can tell
 * "no profile yet" from "a profile with nothing on it yet". There is no
 * readiness score and no employer match anywhere in the response, because a
 * score would be a verdict derived from records that do not support one.
 */
export function fetchCareerSummary(signal?: AbortSignal): Promise<CareerSummaryRead> {
  return apiClient.get<CareerSummaryRead>(CAREER_ENDPOINTS.summary, { signal })
}

/**
 * The caller's career profile.
 *
 * A 404 when no profile has been created yet — which is a state to render, not a
 * failure, and the reason `PUT` exists in the first place.
 */
export function fetchCareerProfile(signal?: AbortSignal): Promise<CareerProfileRead> {
  return apiClient.get<CareerProfileRead>(CAREER_ENDPOINTS.profile, { signal })
}

/**
 * Creates or replaces the caller's career profile.
 *
 * A `PUT`, not a `POST`: `career_profiles.user_id` is unique, so this is an
 * upsert keyed on the account and sending the same body twice leaves the same
 * profile rather than a conflict or a second row. `links` replaces the whole
 * list, which is what makes the call idempotent.
 *
 * **Every field here is the user's.** There is no generated summary, no inferred
 * target role and no highlight NEXUS chose; a form may prefill from the profile
 * it just fetched, but what it sends is still what the person wrote.
 */
export function upsertCareerProfile(payload: CareerProfileUpsert): Promise<CareerProfileRead> {
  return apiClient.put<CareerProfileRead>(CAREER_ENDPOINTS.profile, payload)
}

/** Education, work experience and certification records, filtered and paginated. */
export function fetchCareerExperience(
  params: CareerExperienceListParams = {},
  signal?: AbortSignal,
): Promise<CareerExperienceListRead> {
  return apiClient.get<CareerExperienceListRead>(CAREER_ENDPOINTS.experience, {
    query: queryFrom({
      limit: params.limit,
      offset: params.offset,
      kind: params.kind,
    }),
    signal,
  })
}

/**
 * Adds one dated record to the profile.
 *
 * `kind` and `title` are required; the organisation, the dates and the URL are
 * the user's to supply and are never inferred. A certification with no issuer
 * and no date is a valid row that says only what the user said.
 */
export function createCareerExperience(
  payload: CareerExperienceCreatePayload,
): Promise<CareerExperienceRead> {
  return apiClient.post<CareerExperienceRead>(CAREER_ENDPOINTS.experience, payload)
}

/** Career evidence, newest first, filtered and paginated. */
export function fetchCareerEvidence(
  params: CareerEvidenceListParams = {},
  signal?: AbortSignal,
): Promise<CareerEvidenceListRead> {
  return apiClient.get<CareerEvidenceListRead>(CAREER_ENDPOINTS.evidence, {
    query: queryFrom({
      limit: params.limit,
      offset: params.offset,
      evidence_type: params.evidence_type,
      project_id: params.project_id,
      skill_id: params.skill_id,
      occurred_from: params.occurred_from,
      occurred_to: params.occurred_to,
    }),
    signal,
  })
}

/**
 * Adds one piece of career evidence.
 *
 * `source` defaults to `manual`, which is the correct value for anything the
 * user typed and the only one a client should send by hand: claiming a
 * subsystem derivation would be the same forgery as inventing the qualification
 * itself.
 */
export function createCareerEvidence(
  payload: CareerEvidenceCreatePayload,
): Promise<CareerEvidenceRead> {
  return apiClient.post<CareerEvidenceRead>(CAREER_ENDPOINTS.evidence, payload)
}

/**
 * Run the two deterministic learning rules and return what this call newly raised.
 *
 * A `POST` because it writes: a raised suggestion is a row, and re-running a rule
 * refreshes its existing row rather than duplicating it. Only the newly raised
 * rows come back, so a caller can tell a new suggestion from one it has already
 * been shown.
 *
 * The rules are threshold comparisons over the caller's own recorded figures —
 * there is no model behind this endpoint, and none is planned for it.
 */
export function evaluateLearningRecommendations(
  signal?: AbortSignal,
): Promise<RecommendationRead[]> {
  return apiClient.post<RecommendationRead[]>(
    LEARNING_ENDPOINTS.recommendations,
    undefined,
    { signal },
  )
}
