/**
 * Wire types for the Phase 9 learning and career intelligence surface.
 *
 * Mirrors `backend/app/schemas/learning.py` and `backend/app/schemas/career.py`,
 * which in turn project `backend/app/models/learning.py` and
 * `backend/app/models/career.py`.
 *
 * ## What this surface is allowed to say
 *
 * **A level is the user's, or a labelled estimate that shows its working.**
 * There is no field below that is a verdict about a person, and no `skill_score`,
 * no `proficiency`, no `talent`. `SkillRead.current_level` is paired
 * obligatorily with `level_source` — `user_defined` or `system_estimate` — and
 * an estimate is additionally paired with `confidence` and `evidence_count`, so
 * a client cannot render "2/5" without being able to say *who said so*. The
 * sentence the product wants is "Your current self-assessed level is 2/5. NEXUS
 * recorded 6 related learning activities in the last 30 days"; it is not
 * expressible here in any other register, and a field that could have supported
 * the other one was left out on purpose.
 *
 * **Career content is entirely user-supplied.** NEXUS never writes a
 * certification, an employer, a date or an achievement the user did not provide.
 * Every field in `CareerProfileRead`, `CareerExperienceRead` and
 * `CareerEvidenceRead` is something a person typed or something a subsystem
 * recorded *about a record they created*. `CareerEvidenceRead.source` says which
 * of the two it was, and nothing in this file generates a qualification.
 *
 * **Commits are not task completion.** The same vocabulary that keeps
 * `types/developer.ts` honest is kept here: `repository_activity` evidence
 * exists, "6 commits touched Python files" is sayable, "6 Python projects
 * delivered" is not, and there is no field that would let a screen write it by
 * accident. `LearningActivityRead.activity_type` names *what kind of event was
 * recorded*, and `resource_viewed` is in that vocabulary precisely because it is
 * the weakest member — a page was opened, nothing was understood.
 *
 * ## The two rules this file obeys, and why they are written down twice
 *
 * 1. **The backend emits `null`, never an absent key.** Every nullable field is
 *    `T | null` and *required* — never `field?: T`. `target_date: string | null`
 *    means "this goal has no deadline", whereas `target_date?: string` also
 *    covers "this response shape did not bother", and a client that cannot tell
 *    those apart will eventually render the second as the first.
 * 2. **A figure that could not be computed is `null`, never `0`.** So
 *    `LearningFeatureValues.goal_deadline_distance_days` is null when no goal
 *    carries a deadline, because `0` would assert "a goal is due today" — and
 *    `CareerFeatureValues.repositories` is null for an account whose
 *    repositories have never been scanned, because `0` would assert "this
 *    account has no repositories" when the truth is that nobody looked.
 *
 * A real zero is a plain `number`: `progress: 0` on a goal nobody started,
 * `gap: 0` on a skill already at its target, `evidence_count: 0` on a skill
 * with nothing behind it. Where a figure can be *measured and happen to be
 * absent*, the two are told apart by `available` plus `reason_if_unavailable`
 * rather than by the number.
 *
 * ## What was deliberately not built
 *
 * No model, no prediction, no readiness score and no "career fit".
 * `LearningFeatureVectorRead` and `CareerFeatureVectorRead` are *extractors*:
 * named numbers under a `schema_version` so a later phase knows what each column
 * meant. Phase 9 produces features; it does not train, load, serve or register
 * anything, and nothing in this file may be joined with `features` and rendered
 * as a forecast.
 */

import type { ISODateTimeString, UUIDString } from './api'
import type { PaginationParams } from './pagination'
import type { DateOnlyString, ProjectPriority } from './work'

// Re-exported so a consumer of the learning vocabulary needs one import, not four.
// `PaginationParams` is deliberately the only one of these three the barrel
// re-exports from this module: it already comes from './pagination', and a second
// export of the same name collides.
export type { ISODateTimeString, PaginationParams, UUIDString }

/* -------------------------------------------------------------- vocabulary */

/**
 * Where a learning goal sits in its own life.
 *
 * `archived` is separate from `completed` because a finished goal the user still
 * wants as a record and a goal they have dismissed are different facts — and
 * because an archived goal must not be counted as outstanding work anywhere. A
 * client that renders `completed` and `archived` with the same badge has merged
 * those two states and will say "3 goals remaining" on an account that finished
 * them all.
 */
export const LEARNING_GOAL_STATUSES = [
  'not_started',
  'in_progress',
  'paused',
  'completed',
  'archived',
] as const
export type LearningGoalStatus = (typeof LEARNING_GOAL_STATUSES)[number]

/**
 * Who is allowed to claim a number for a skill level.
 *
 * This is the single most important honesty control in the phase, and it is
 * typed as a closed union rather than left as free text so that no screen can
 * render a level without also rendering where it came from. A `user_defined`
 * level is a claim the person is making and NEXUS merely records; a
 * `system_estimate` is an inference it must be able to show its working for, and
 * carries `confidence` and `evidence_count` beside it.
 *
 * There is no third member. A level nobody can attribute is not a level.
 */
export const SKILL_LEVEL_SOURCES = ['user_defined', 'system_estimate'] as const
export type SkillLevelSource = (typeof SKILL_LEVEL_SOURCES)[number]

/**
 * What kind of event counts as evidence that something was learned.
 *
 * Each member is a fact NEXUS can point at a record for, and none of them
 * implies understanding. `resource_viewed` is in the set because a page really
 * was opened and deleting it would make the record incomplete — but it is the
 * weakest member and is weighted as such by the backend, which is why a client
 * should not lump it in with `concept_learned` on a chart.
 *
 * `coding_activity` is labelled by what it *is* — a recorded event — and never
 * by what it produced. A commit is not a task completion.
 */
export const LEARNING_ACTIVITY_TYPES = [
  'study_session',
  'task_completed',
  'note_created',
  'resource_viewed',
  'concept_learned',
  'project_completed',
  'coding_activity',
] as const
export type LearningActivityType = (typeof LEARNING_ACTIVITY_TYPES)[number]

/**
 * A thing worth putting in front of someone deciding about the user.
 *
 * `certification` is here because the *user* may have one, not because NEXUS
 * issues one. Nothing on this surface creates a credential; a certification
 * exists only as a `career_experience` row or a `career_evidence` row the user
 * typed, with a title and a date they supplied.
 *
 * `repository_activity` is deliberately named after the record it came from.
 * "6 commits touched Python files in this repository" is true and derivable;
 * "delivered 6 Python projects" is a different claim and has no field here.
 */
export const CAREER_EVIDENCE_TYPES = [
  'project_completed',
  'feature_shipped',
  'repository_activity',
  'skill_activity',
  'learning_milestone',
  'certification',
  'achievement',
] as const
export type CareerEvidenceType = (typeof CAREER_EVIDENCE_TYPES)[number]

/**
 * A line on the career profile that is a *record* rather than an achievement.
 *
 * Education, work experience and certification are dated entries — the CV
 * section, not the achievements section. Splitting them means "I worked here
 * until March" can never be rendered with the same weight as "I shipped X", and
 * `CareerExperienceListRead.by_kind` can count them separately. The summary's
 * `record_count` deliberately does not: they share one table, and the per-kind
 * split is one request away.
 */
export const CAREER_RECORD_KINDS = ['education', 'experience', 'certification'] as const
export type CareerRecordKind = (typeof CAREER_RECORD_KINDS)[number]

/**
 * How wide one bucket of the activity series is.
 *
 * Mirrors `ActivityGranularity` in `app/services/learning/metrics.py`. Declared
 * here rather than shared with the developer series because the two vocabularies
 * are separate Python enums, and a union that covered both would let a client ask
 * for a grain the learning endpoint rejects.
 */
export const ACTIVITY_GRANULARITIES = ['day', 'week', 'month'] as const
export type ActivityGranularity = (typeof ACTIVITY_GRANULARITIES)[number]

/**
 * What kind of figure a learning metric's number is.
 *
 * Carried as data rather than baked into formatting, so a card cannot render a
 * ratio as a count of hours. The set is the Phase 8 unit set minus `lines` and
 * `score`: nothing on this surface counts changed lines, and nothing scores a
 * person.
 */
export const LEARNING_METRIC_UNITS = ['count', 'minutes', 'days', 'ratio', 'percent'] as const
export type LearningMetricUnit = (typeof LEARNING_METRIC_UNITS)[number]

/**
 * The schema version stamped on a learning feature vector.
 *
 * A *closed* union on purpose, the same argument `DEVELOPER_FEATURE_SCHEMA_VERSIONS`
 * makes: the entire reason the vector carries a version is that a later trainer
 * must be able to tell what each column meant, and widening this to `string`
 * would let `learning_features.v2` typecheck silently against v1 field meanings.
 */
export const LEARNING_FEATURE_SCHEMA_VERSIONS = ['learning_features.v1'] as const
export type LearningFeatureSchemaVersion = (typeof LEARNING_FEATURE_SCHEMA_VERSIONS)[number]

/** The same argument, for `GET /career/features`. */
export const CAREER_FEATURE_SCHEMA_VERSIONS = ['career_features.v1'] as const
export type CareerFeatureSchemaVersion = (typeof CAREER_FEATURE_SCHEMA_VERSIONS)[number]

/** Mirrors `learning_default_window_days`. Sending nothing asks for this. */
export const LEARNING_DEFAULT_WINDOW_DAYS = 30

/** Mirrors `learning_max_window_days`. A wider window is a 422 server-side. */
export const MAX_LEARNING_WINDOW_DAYS = 366

/**
 * The level scale, as the two numbers the backend constrains it with.
 *
 * A skill level is typed `number` rather than `1 | 2 | 3 | 4 | 5` on purpose:
 * widening the scale later must be a type change the compiler finds everywhere,
 * not a database migration. The bound is still stated here so a form can clamp
 * and a chart can draw a 5-cell gauge without hard-coding the number twice.
 *
 * Five is enough to be useful and few enough that the difference between 3 and 4
 * means something.
 */
export const MIN_SKILL_LEVEL = 1
export const MAX_SKILL_LEVEL = 5

/**
 * Mirrors `learning_min_evidence_for_estimate`.
 *
 * Below this many recorded activities the backend refuses to offer an estimate
 * at all and says so, rather than inventing a level. A client that wants a
 * "suggested level" affordance must gate it on this count too.
 */
export const LEARNING_MIN_EVIDENCE_FOR_ESTIMATE = 3

/**
 * Mirrors `career_stale_inactive_days`.
 *
 * When a target skill has gone longer than this without recorded activity, a
 * target skill counts as dormant. It is a threshold on *records*, not a claim
 * that anybody stopped learning.
 */
export const CAREER_STALE_INACTIVE_DAYS = 21

/** Mirrors `learning_max_goals`. Hitting it is a 409, not a silent drop. */
export const LEARNING_MAX_GOALS = 200

/** Mirrors `learning_max_skills`. */
export const LEARNING_MAX_SKILLS = 100

/** Mirrors `career_max_evidence`. */
export const CAREER_MAX_EVIDENCE = 500

/**
 * The copy used whenever a figure could not be measured.
 *
 * Phase 8 keeps a module constant of the backend's sentence so the two cannot
 * drift into two different strings for one condition. This one is frontend-only
 * — the backend answers with its own `reason_if_unavailable`, and this exists so
 * a chart, a stat tile and a form hint all render *one* phrase instead of three.
 * It is named differently from `types/developer.ts`'s `NOT_ENOUGH_DATA` on
 * purpose: the two sentences differ, and collapsing them into one exported
 * constant would make the difference unrepresentable.
 */
export const INSUFFICIENT_DATA_MESSAGE = 'Not enough data yet.'

/* --------------------------------------------------------------- list params */

/**
 * The window-only parameter set, shared by the summary, metrics, gaps, activity
 * and feature reads — the five endpoints that reason over a range and nothing
 * else.
 *
 * Optional because the backend owns the default. A client that invented its own
 * fallback would show a 30-day chart on a page the server answered for a
 * different range, and the disagreement would be invisible.
 */
export interface LearningWindowParams {
  /** Left unset, the backend applies `learning_default_window_days`. */
  window_days?: number
}

/**
 * Query parameters for `GET /learning/goals`.
 *
 * `status` is served from `ix_learning_goals_owner_status` and
 * `target_skill_id` from the same owner's rows, so both are filters the backend
 * can answer without a scan. Neither is defaulted here: omitting them asks for
 * every goal the account owns, which is what the list page wants on first load.
 */
export interface LearningGoalListParams extends PaginationParams {
  status?: LearningGoalStatus
  target_skill_id?: UUIDString
  project_id?: UUIDString
  /** Only goals whose `target_date` falls on or before this date. */
  target_before?: DateOnlyString
  /** Only goals whose `target_date` falls on or after this date. */
  target_after?: DateOnlyString
}

/**
 * Query parameters for `GET /learning/skills`.
 *
 * `category` is free text rather than a closed union, mirroring the column:
 * `language`, `framework`, `domain` and `practice` are suggestions the UI offers,
 * not a set the schema enforces, and a closed union here would reject a category
 * the user typed and the database accepted.
 */
export interface SkillListParams extends PaginationParams {
  category?: string
  /** Only skills whose last recorded activity falls on or after this date. */
  active_since?: DateOnlyString
}

/**
 * Query parameters for `GET /learning/gaps`.
 *
 * `window_days` decides the window `evidence_last_30d` is counted over, and
 * `skill_id` narrows the answer to one skill. Neither changes what a gap *is* —
 * `gap` is always `max(0, target_level - current_level)` — only how much
 * evidence the explanation beside it can cite.
 */
export interface SkillGapParams extends LearningWindowParams {
  skill_id?: UUIDString
}

/**
 * Query parameters for `GET /learning/activities`.
 *
 * `occurred_from` and `occurred_to` bound the trail directly, while
 * `window_days` asks for the configured relative window. Both are offered
 * because "since March" and "last 30 days" are genuinely different questions and
 * a client should not have to approximate one with the other.
 */
export interface LearningActivityListParams extends PaginationParams {
  skill_id?: UUIDString
  goal_id?: UUIDString
  activity_type?: LearningActivityType
  window_days?: number
  occurred_from?: DateOnlyString
  occurred_to?: DateOnlyString
}

/** Query parameters for `GET /career/experience`. */
export interface CareerExperienceListParams extends PaginationParams {
  kind?: CareerRecordKind
}

/** Query parameters for `GET /career/evidence`. */
export interface CareerEvidenceListParams extends PaginationParams {
  evidence_type?: CareerEvidenceType
  project_id?: UUIDString
  skill_id?: UUIDString
  occurred_from?: DateOnlyString
  occurred_to?: DateOnlyString
}

/* -------------------------------------------------------------------- shapes */

/**
 * One learning goal: something the user said they were working towards.
 *
 * The whole row is user-authored intent. `progress` is a percentage the user set
 * or moved — NEXUS does not compute it from activity, because a percentage
 * derived from the absence of an activity would be a claim about commitment
 * rather than about progress.
 *
 * `target_skill_id` and `target_topic` are the two forms of the same idea and
 * both may be null: a goal can name a skill that does not exist yet (the FK is
 * `ON DELETE SET NULL`, so it can also be set aside later), or describe its
 * subject in words. A screen must handle "neither" by showing the title alone,
 * which is why neither is required.
 *
 * `completed_at` is set by the complete route, not by an edit, and the backend
 * refuses to store it against a non-terminal status.
 */
export interface LearningGoalRead {
  id: UUIDString
  title: string
  /** The user's own words about the goal. Never generated. */
  description: string | null
  /** The skill this goal is for, or null when the topic is not a tracked skill —
   *  or when the skill it named has since been deleted. */
  target_skill_id: UUIDString | null
  /** The free-text form of the same idea, for a subject with no skill row yet. */
  target_topic: string | null
  /** `YYYY-MM-DD`. Null means "no deadline", which is a legitimate state and is
   *  never inferred from a zero day-count. */
  target_date: DateOnlyString | null
  /** Reuses the existing project priority vocabulary rather than a second,
   *  near-identical one. */
  priority: ProjectPriority
  status: LearningGoalStatus
  /** 0–100, the user's own figure. A real zero means not started. */
  progress: number
  /** The user's own estimate of effort, in minutes. Null when they gave none —
   *  NEXUS has no opinion and will not supply one. */
  estimated_effort_minutes: number | null
  project_id: UUIDString | null
  /** The note this goal relates to, if any. Null is normal. */
  note_id: UUIDString | null
  /** Set by the complete route. Null for every other status. */
  completed_at: ISODateTimeString | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/**
 * One page of learning goals.
 *
 * Flat `items`/`total`/`limit`/`offset` rather than the shared `Paginated<T>`
 * envelope, matching the Phase 7 and Phase 8 list shapes: these totals are read
 * next to the rows on screen, and burying them under `meta` invites a header to
 * be written against a page slice and then quoted as the whole.
 *
 * The two breakdowns count **every goal matching the filters, not just this
 * page**, so a card cannot quote a status tally the pager contradicts. Every
 * member of {@link LEARNING_GOAL_STATUSES} is a key of `by_status`, including
 * the ones at zero.
 */
export interface LearningGoalListRead {
  items: LearningGoalRead[]
  /** Goals matching the filters, not the length of this page. */
  total: number
  limit: number
  offset: number
  /** Goals per {@link LearningGoalStatus} across every matching goal. */
  by_status: Record<string, number>
  /** One factual sentence describing the counts, composed server-side. */
  summary: string
}

/**
 * One skill and the level the user claims for it, or the one NEXUS estimated.
 *
 * `current_level` and `level_source` are read together or not at all. The
 * backend refuses an estimate below `learning_min_evidence_for_estimate`
 * activities rather than offering one, so a `system_estimate` row here always
 * has `evidence_count` to back it and `confidence` to say how firmly.
 *
 * `confidence` is 0–100 and means *how much evidence backs the estimate*, never
 * how good the user is. A skill with `level_source: 'user_defined'` carries
 * `confidence: 0` as a plain measurement, not as a hole.
 *
 * `last_activity_at` is null for a skill nothing has been recorded against; that
 * is different from one whose last activity is old, which is what the
 * recommendation rules and the gap explanations read.
 */
export interface SkillRead {
  id: UUIDString
  name: string
  /** `language`, `framework`, `domain`, `practice` are suggestions, not a
   *  closed set. Null when the user did not categorise it. */
  category: string | null
  description: string | null
  /** 1–5. The user's level, or a labelled system estimate. Never a verdict. */
  current_level: number
  /** 1–5. Where the user wants to be, which is their claim too. */
  target_level: number
  level_source: SkillLevelSource
  /** 0–100. How much recorded evidence backs an estimate. Zero for a
   *  user-defined level, and that zero is a real measurement. */
  confidence: number
  /** Learning activities recorded against this skill, all time. A real zero. */
  evidence_count: number
  /** Null when nothing has ever been recorded against it. */
  last_activity_at: ISODateTimeString | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/**
 * One page of skills.
 *
 * The three tallies below count **every skill matching the filters, not just this
 * page**, so a header cannot quote a figure the pager contradicts.
 * `skills_with_evidence` and `skills_without_evidence` are a real zero-pair: they
 * say how many tracked skills have recorded activity behind them, which is not
 * the same claim as "this account has no skills" (`total: 0`).
 */
export interface SkillListRead {
  items: SkillRead[]
  total: number
  limit: number
  offset: number
  /** Skills per {@link SkillLevelSource}, so no level is counted without its
   *  origin. */
  by_level_source: Record<string, number>
  /** Skills per category, across every matching skill. */
  by_category: Record<string, number>
  /** Skills with at least one recorded activity. A real zero. */
  skills_with_evidence: number
  /** Skills with none. A real zero, and not a measurement of ability. */
  skills_without_evidence: number
}

/**
 * The distance between a skill's current level and its target, computed on read.
 *
 * A gap is **never stored**. It is derived on every read from `Skill` and from
 * the activities recorded against it, for the same reason weekly and monthly
 * analytics metrics are not stored: a stored copy would be a second answer that
 * could disagree with the dashboard.
 *
 * Six fields do the honesty work here:
 *
 * - `level_source` decides whether `current_level` may be called *self-assessed*
 *   or must be attributed as a *system estimate*. It is present on the gap for
 *   exactly that reason: the number on a card is never unattributed.
 * - `gap` is `max(0, target_level - current_level)` and a real `0` when the user
 *   has reached their target. That is a measurement.
 * - `available` with `reason_if_unavailable` is how "nothing was recorded at all"
 *   is told apart from "there is no gap". `available: false` means the levels
 *   could not be compared; it never carries a zero.
 * - `days_since_last_activity` is null for a skill with no recorded activity.
 *   `0` would claim activity happened today.
 * - `evidence_last_30d` counts `learning_activities` rows inside the window. It
 *   is a count of records, never of hours or of understanding.
 * - `explanation` is one sentence naming both levels *and* the evidence count,
 *   e.g. "Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related
 *   learning activities in the last 30 days." The backend refuses to build one
 *   without a digit in it, so a screen can show it verbatim.
 */
export interface SkillGapRead {
  /** Null for a gap about a topic that has no skill row yet — a goal can name a
   *  subject before the skill exists. */
  skill_id: UUIDString | null
  skill_name: string
  target_level: number
  current_level: number
  /** Whether to say "self-assessed" or "system estimate". Never omit this. */
  level_source: SkillLevelSource
  /** `max(0, target - current)`. Zero is a real measurement and is paired with
   *  `available: true`. */
  gap: number
  /** Activities recorded against this skill, all time. */
  evidence_count: number
  /** Activities recorded inside the window the explanation describes. */
  evidence_last_30d: number
  /** Null when no activity has ever been recorded for this skill. */
  days_since_last_activity: number | null
  available: boolean
  /** Why the gap could not be measured. Null whenever `available` is true, and
   *  the reason a screen shows the placeholder rather than a zero. */
  reason_if_unavailable: string | null
  explanation: string
}

/**
 * One page of skill gaps.
 *
 * Carries `available: false` rows as well as measurable ones, because
 * "nothing has been recorded for this skill" is the answer on a cold account and
 * the page has to be able to say so rather than render an empty chart.
 *
 * The four figures after the page bounds count **every gap matching the
 * filters, not just this page**, and they are the tally the panel is built
 * around: `total` is how many gaps exist, and `available_count` /
 * `unavailable_count` split them into the rows that could be compared and the
 * rows that could not. `by_level_source` is a gap count per
 * {@link SkillLevelSource}, so no level appears in a total without saying where
 * it came from. Collapsing this envelope to `items` would drop all four.
 */
export interface SkillGapListRead {
  items: SkillGapRead[]
  /** Gaps matching the filters, not the length of this page. */
  total: number
  limit: number
  offset: number
  /** Gaps whose two levels could be compared. */
  available_count: number
  /** Gaps whose levels could not be compared, each carrying its own reason. */
  unavailable_count: number
  /** Gaps per {@link SkillLevelSource}, across every matching gap. */
  by_level_source: Record<string, number>
}

/**
 * One recorded learning event, and where it came from.
 *
 * Append-only: there is no `updated_at`, because a recorded activity is a fact
 * about a moment and amending it would make the trail a claim rather than a
 * record. `created_at` and `occurred_at` differ for an activity recorded after
 * the fact, and both are carried because "when it happened" and "when NEXUS
 * learned of it" are different questions.
 *
 * `duration_minutes` is null when the activity is an event rather than a span.
 * That is a real distinction — a page was opened, it did not last a duration —
 * and it is the reason the field is nullable rather than zero.
 *
 * `source_type` and `source_id` are a polymorphic pair, exactly as
 * `RiskRead.entity_type`/`entity_id` are elsewhere in this codebase. A `null`
 * pair means the user typed it in; anything else names the record it was derived
 * from. Neither implies the activity completed anything: `task_completed` names
 * the *kind of event recorded*, and a `coding_activity` is a recorded code
 * event, not a delivered task.
 */
export interface LearningActivityRead {
  id: UUIDString
  /** Null for a study session that names no skill yet. */
  skill_id: UUIDString | null
  /** Null once the goal has been deleted — the trail outlives the goal, the FK
   *  is `ON DELETE SET NULL`. */
  goal_id: UUIDString | null
  activity_type: LearningActivityType
  title: string
  description: string | null
  /** When the activity happened. */
  occurred_at: ISODateTimeString
  /** Null when the activity is an event rather than a span. */
  duration_minutes: number | null
  /** `manual`, `task`, `note`, `project` or `repository`, or null when the user
   *  typed it in with no backing record. */
  source_type: string | null
  /** The record the activity was derived from. Null pairs with `source_type`. */
  source_id: UUIDString | null
  created_at: ISODateTimeString
}

/**
 * One page of learning activities, newest first.
 *
 * `by_type` counts **every activity matching the filters, not just this page**,
 * and is what stops a chart presenting `resource_viewed` — a page was opened —
 * as equivalent to `concept_learned`. Every member of
 * {@link LEARNING_ACTIVITY_TYPES} is a key, including the ones at zero.
 */
export interface LearningActivityListRead {
  items: LearningActivityRead[]
  total: number
  limit: number
  offset: number
  /** Activities per {@link LearningActivityType}, across every match. */
  by_type: Record<string, number>
  /** One factual sentence describing the counts, composed server-side. */
  summary: string
}

/**
 * One bucket of the activity series.
 *
 * Buckets are **zero-filled**: a quiet Tuesday arrives with `activity_count: 0`
 * rather than being skipped, because a series that omits empty buckets silently
 * compresses the timeline and makes a sparse fortnight read as dense as a busy
 * one. `minutes` is zero for the same reason — activities that happened but
 * carried no duration sum to zero minutes, which is a measurement.
 */
export interface LearningActivityBucketRead {
  bucket_start: ISODateTimeString
  bucket_end: ISODateTimeString
  /** Activities recorded in this bucket, all types. A real zero for a bucket
   *  that recorded nothing — the series is dense, so gaps are zeroes rather
   *  than missing rows. */
  activities: number
  /** Of those, the ones typed `study_session`. The two counts do not move
   *  together, and a chart showing only the raw one would overstate a
   *  fortnight of page views. */
  sessions: number
  /** Recorded minutes, or null when nothing in this bucket carried a
   *  duration. Null rather than 0: no duration recorded is not a measured
   *  absence of time. */
  minutes: number | null
}

/**
 * The activity series, with the window that produced it.
 *
 * `total_minutes` is null when no bucket measured any time — a sum of empty
 * buckets is arithmetically 0 and factually wrong.
 */
export interface LearningActivitySeriesRead {
  granularity: ActivityGranularity
  window_days: number
  window_start: ISODateTimeString
  window_end: ISODateTimeString
  /** The skill the series was narrowed to, or null across the whole account. */
  skill_id: UUIDString | null
  buckets: LearningActivityBucketRead[]
  total_activities: number
  total_minutes: number | null
}

/**
 * The account-wide headline figures, for the learning dashboard.
 *
 * Counts of records and of recorded minutes. There is no score here, no
 * comparison to a previous month as a verdict, and no field that ranks the user.
 * `activities_in_window` is "how many learning activities were recorded", and the
 * window it covers is carried beside it so the sentence printed underneath it can
 * be true.
 *
 * `has_data` is the cold-start flag: false means the counts are legitimately
 * zero *and* the page must explain that nothing has been recorded, rather than
 * rendering a dashboard of zeroes as a finding about the person.
 *
 * Note what is absent: there is no `average_level` and no `strongest_skill`.
 * Both would be a claim about the user with no attribution, which is the one
 * thing this surface may not make.
 */
export interface LearningSummaryRead {
  /** Goals recorded for this account, archived ones included. */
  goal_count: number
  /** Goals not completed and not archived. */
  active_goal_count: number
  /** Goals in `completed`. A count of goals, never a completion *rate*. */
  completed_goal_count: number
  skill_count: number
  /** Tracked skills with at least one recorded activity behind them. A real
   *  zero, and not a measurement of how good the skills are. */
  skills_with_evidence: number
  activity_count: number
  /** Activities recorded inside the window below. A real zero when none were. */
  activities_in_window: number
  /** Recorded minutes inside the window. Null when no activity in it carried a
   *  duration: a sum over activities that recorded no time is a measurement of
   *  nothing, and `0` would assert a measured absence of minutes. */
  minutes_in_window: number | null
  window_days: number
  window_start: ISODateTimeString
  window_end: ISODateTimeString
  /** Null for an account with no recorded activity at all. */
  latest_activity_at: ISODateTimeString | null
  /** False when there is nothing to summarise, so zeros read as absence. */
  has_data: boolean
  /** One factual sentence describing the counts, composed server-side. */
  summary: string
}

/**
 * One metric, fully explained.
 *
 * The same shape `DeveloperMetricRead` carries, for the same reasons:
 *
 * - `value` is `number | null`, and null means *not measured*. A ratio with a
 *   zero denominator produces null rather than an invented zero.
 * - `available` with `reason_if_unavailable` is the positive form of the same
 *   fact. `value: 0` with `available: true` is a *different* answer — the
 *   arithmetic came out at zero — and must never be rendered with the reason
 *   attached.
 * - `definition` says how it is computed; `explanation` says it again with the
 *   figures in it, and always contains a digit.
 * - `key` is a plain `string` rather than a closed union, unlike Phase 8's
 *   `DeveloperMetricKey`, because the Phase 9 contract freezes the feature
 *   vector's column names but not the metric key vocabulary. Declaring a closed
 *   set here would be the client inventing a contract the server never agreed to;
 *   a screen should therefore switch on the label or fall back, never index a
 *   lookup table it assumed was complete.
 */
export interface LearningMetricRead {
  key: string
  label: string
  /** Null when the metric could not be computed. Never 0 for that reason. */
  value: number | null
  unit: LearningMetricUnit
  /** One sentence naming the inputs and the arithmetic. */
  definition: string
  /** The window this instance measured, or null for a whole-history figure. */
  window_days: number | null
  /** Which recorded facts the computation read, e.g. `learning_activities`. */
  source: string
  /** The sentence shown to the user, carrying the figures it was built from. */
  explanation: string
  available: boolean
  /** Why it is unavailable; null when `available` is true. */
  reason_if_unavailable: string | null
}

/**
 * The feature names, and only the feature names — `learning_features.v1`.
 *
 * An extractor, not a model. Nothing here is a prediction, a probability or a
 * fitted parameter, and `skill_activity_frequency` is a rate of *recorded
 * activities per skill per day*, never a statement about a person.
 *
 * The nullable figures are the null-not-zero rule made concrete:
 * `goal_progress` is null when the account has no goals, `goal_deadline_distance_days`
 * is null when no goal carries a deadline (0 would assert one is due today), and
 * `learning_consistency` is null when the window is too short to have a
 * denominator.
 */
export interface LearningFeatureValues {
  sessions_last_7d: number
  sessions_last_30d: number
  /** Recorded minutes across the window. Zero when every activity in it was an
   *  event rather than a span. */
  learning_minutes: number
  /** Mean progress across the account's goals. Null when there are none. */
  goal_progress: number | null
  /** Days from now to the nearest goal deadline. Null when no goal has one. */
  goal_deadline_distance_days: number | null
  /** Completed goals over all goals. Null when there are no goals at all. */
  completion_rate: number | null
  /** Distinct active days over active days in the window. Null when the window
   *  is too short or nothing was recorded. */
  learning_consistency: number | null
  /** Recorded activities per skill per day. Null when no skill is tracked. */
  skill_activity_frequency: number | null
}

/** `GET /learning/features`. */
export interface LearningFeatureVectorRead {
  schema_version: LearningFeatureSchemaVersion
  generated_at: ISODateTimeString
  /** The window the rates above were computed over. */
  window_days: number
  features: LearningFeatureValues
}

/**
 * The user's career profile. Entirely their own words.
 *
 * There is no `summary` written by NEXUS on this type — `summary` here is the
 * user's own paragraph, field for field with the column, and a screen must never
 * replace it with a generated one. A profile that does not exist yet is a 404 on
 * the read and the target of the `PUT` upsert, so "no profile" and "empty
 * profile" are different states and the page has to be able to tell them apart.
 *
 * `links` is a list of portfolio URLs the user supplied. It is a plain
 * `string[]` and never `null`: "no links yet" is an empty list, which is a fact
 * about the profile rather than a missing field.
 */
export interface CareerProfileRead {
  id: UUIDString
  target_role: string | null
  target_domain: string | null
  /** A one-line self-description. The user's words, like everything else here. */
  headline: string | null
  /** The user's own paragraph. Never generated, never rewritten. */
  summary: string | null
  location: string | null
  /** Portfolio URLs the user supplied. Empty when none have been added. */
  links: string[]
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/**
 * One dated record on the profile: education, work experience or a
 * certification the user holds.
 *
 * `ended_on` is null for something current, which is a real state and the normal
 * one for employment in progress — not an unknown date. `started_on` is null when
 * the user did not say, and the backend refuses a pair that runs backwards
 * (`ended_on >= started_on`).
 *
 * Nothing in this row was inferred. If NEXUS had guessed at an employer or a
 * graduation date the row would be a lie the user then had to correct, so the
 * only fields that can be non-null here are the ones someone typed.
 */
export interface CareerExperienceRead {
  id: UUIDString
  kind: CareerRecordKind
  title: string
  organisation: string | null
  started_on: DateOnlyString | null
  /** Null means current. */
  ended_on: DateOnlyString | null
  description: string | null
  url: string | null
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/**
 * One page of career records.
 *
 * `by_kind` counts records per {@link CareerRecordKind} across **every matching
 * record, not just this page**, which is what keeps a CV section from being read
 * as an achievements section. `current_count` is the subset whose `ended_on` is
 * null — a role in progress, not a record of unknown length.
 */
export interface CareerExperienceListRead {
  items: CareerExperienceRead[]
  total: number
  limit: number
  offset: number
  /** Records per {@link CareerRecordKind}, across every matching record. */
  by_kind: Record<string, number>
  /** Records whose `ended_on` is null: something current, not something unknown. */
  current_count: number
  /** One factual sentence describing the counts, composed server-side. */
  summary: string
}

/**
 * One piece of evidence the user put forward, or that a subsystem recorded
 * about a record they created.
 *
 * `source` says which: `manual` for something the user typed, otherwise the
 * subsystem the row was derived from. A client may present a derived row as
 * derived; what it may never do is present one as something the user asserted,
 * or generate one the user did not supply. `certification` in
 * {@link CareerEvidenceRead.evidence_type} exists because the *user* has one.
 *
 * The three nullable foreign keys are what makes deduplication work on the
 * server — nulls do not collide in a btree unique index, so several manually
 * added `achievement` rows coexist while a project-derived one cannot be
 * inserted twice — and a row with none of them set is perfectly normal.
 */
export interface CareerEvidenceRead {
  id: UUIDString
  evidence_type: CareerEvidenceType
  title: string
  description: string | null
  /** `YYYY-MM-DD`. Not nullable: evidence without a date could not be ordered. */
  occurred_on: DateOnlyString
  project_id: UUIDString | null
  skill_id: UUIDString | null
  repository_id: UUIDString | null
  /** `manual`, or the subsystem the row was derived from. */
  source: string
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
}

/** One page of career evidence, newest first. */
export interface CareerEvidenceListRead {
  items: CareerEvidenceRead[]
  total: number
  limit: number
  offset: number
  /** Counts keyed by evidence type across every matching row, not just this
   *  page, so a header cannot quote a total the pager contradicts. */
  by_type: Record<string, number>
  /** Counts keyed by `CareerEvidenceRead.source`, so a derived row is never
   *  counted as something the person asserted. */
  by_source: Record<string, number>
  /** Rows whose `source` is `manual`: what the user typed. A real zero. */
  manual_count: number
  /** One factual sentence describing the counts, composed server-side. */
  summary: string
}

/**
 * The career dashboard's headline figures — `GET /career/summary`.
 *
 * Counts only. There is no readiness score, no employer match and no "you are a
 * good fit for X" anywhere in this type — a score would be a verdict about a
 * person derived from records that do not support one, which is the failure this
 * whole phase is built to avoid.
 *
 * `has_profile` is separate from the counts so the page can distinguish "no
 * profile yet, here is the empty state" from "a profile with nothing on it yet".
 * `latest_evidence_on` is null when no evidence has been recorded, and
 * `has_data` is false in that case too — the cold-start flag.
 *
 * ## Why this type once declared six fields the route does not send
 *
 * It used to carry `experience_count`, `education_count`, `certification_count`,
 * `linked_evidence_count`, `by_type` and `target_domain`. **`GET /career/summary`
 * sends none of them.** A client reading them got `undefined` for a figure the
 * backend had counted, and rendered this project's own "not measured" dash —
 * the one word on the surface reserved for a number the server declined to
 * produce — over a field it had simply misnamed. The audit is recorded here
 * rather than only in a commit, because the next field added to this interface
 * has to be read off a real body:
 *
 * - **There is no per-kind record count on the summary.** `record_count` counts
 *   education, work experience and certifications together, because they share
 *   one table. The per-kind split is `by_kind` on {@link CareerExperienceListRead},
 *   and a screen that needs it has to read that response.
 * - **There is no `by_type` on the summary either.** The per-kind evidence
 *   breakdown lives on {@link CareerEvidenceListRead}, where it is keyed by
 *   {@link CareerEvidenceType} and narrowed by whatever filter the request
 *   carried. Reading it from the summary yielded a permanent dash for every row.
 * - **`target_domain` is on the profile, not here.** The summary echoes
 *   `target_role` so a header need not join two requests, and echoes nothing
 *   else; the domain is read off {@link CareerProfileRead}.
 * - **`linked_evidence_count` was never a count of rows.** What the route sends is
 *   `linked_project_count`, and it counts *distinct projects* the evidence points
 *   at, not evidence rows. Reading one as the other would have put a project's
 *   count under a row's label.
 *
 * Every remaining count is a plain `number`, and every one of them is a real
 * measurement: `0` here means "the account holds none", which is always knowable,
 * and the cold-start case is carried by `has_data` instead of by a null.
 */
export interface CareerSummaryRead {
  /** False when `PUT /career/profile` has never been called for this account. */
  has_profile: boolean
  /** Echoed from the profile so a header need not join two requests. Null when
   *  the user has not said what they are aiming at. */
  target_role: string | null
  /** Portfolio URLs on the profile. Zero means none were supplied, which is an
   *  answer rather than a missing measurement. */
  link_count: number
  /** Education, work experience and certifications **together** — they share one
   *  table. Per-kind counts are `by_kind` on {@link CareerExperienceListRead}. */
  record_count: number
  /** Evidence rows across the whole history. A real zero on a new account. */
  evidence_count: number
  /** Of those, how many were dated inside the window below. */
  evidence_in_window: number
  /** Of those, how many the user entered by hand. The one provenance figure the
   *  summary states: it separates what the person wrote from what a subsystem
   *  observed about a record they created. */
  manual_evidence_count: number
  /** **Distinct projects** this account's evidence points at, not a count of
   *  evidence rows. A btree-unique join would otherwise let one popular project
   *  be counted once per row that names it. */
  linked_project_count: number
  /** Projects on this account, completed ones included. Carried beside
   *  `completed_project_count` so the completed figure has its denominator. */
  project_count: number
  /** Of those, the ones that reached `completed`. Read from the project's own
   *  status column, never inferred from the evidence table. */
  completed_project_count: number
  /** Repositories registered, whether or not any has been scanned. Zero means
   *  none were registered, which is a different fact from "registered but never
   *  read" — a scan state this surface deliberately does not assert. */
  repository_count: number
  /** Tracked skills carrying at least one career evidence row. A count of the
   *  user's own claims about their skills, not a measurement of ability. */
  skills_with_evidence: number
  /** Learning activities recorded across the account, whole history. Read from
   *  the learning tables, so the career page and the learning page cannot quote
   *  different totals for the same rows. */
  learning_activity_count: number
  /** Carried because `evidence_in_window` is date-bounded, and a sentence printed
   *  above it that omitted the range would not be true. */
  window_days: number
  window_start: ISODateTimeString
  window_end: ISODateTimeString
  /** `YYYY-MM-DD` of the most recent evidence. Null when there is none. */
  latest_evidence_on: DateOnlyString | null
  /** False when there is nothing to summarise, so zeros read as absence. */
  has_data: boolean
  /** One factual sentence describing the counts. Never a suitability claim. */
  summary: string
}

/**
 * The feature names, and only the feature names — `career_features.v1`.
 *
 * An extractor. Nothing here is a prediction or a match score, and
 * `relevant_skill_evidence` is a count of evidence rows that link to a skill —
 * never a claim that the user is good at anything.
 *
 * The nullable figures are where absence of measurement is refused: `repositories`
 * is null for an account whose repositories have never been scanned (`0` would
 * assert "no repositories exist"), `projects_completed` is null when the account
 * has no projects to have completed any, and `relevant_skill_evidence` is null
 * when nothing has been recorded at all rather than a flattering zero.
 */
export interface CareerFeatureValues {
  /** Projects marked complete. Null when the account has no projects — a zero
   *  there would be a claim about a portfolio that was never looked at. */
  projects_completed: number | null
  /** Recorded activity against the account's projects. Null when there are none. */
  project_activity: number | null
  /** Repositories the account has registered. Null when none have been
   *  registered or scanned. */
  repositories: number | null
  /** Evidence rows linked to a skill. Null when no evidence has been recorded. */
  relevant_skill_evidence: number | null
  /** Learning activities recorded in the window. Null when none have been. */
  learning_activity: number | null
  /** Links on the career profile. Plain `number`: zero means the user has not
   *  added any, which is always measurable and always known. */
  portfolio_evidence_count: number
}

/** `GET /career/features`. */
export interface CareerFeatureVectorRead {
  schema_version: CareerFeatureSchemaVersion
  generated_at: ISODateTimeString
  window_days: number
  features: CareerFeatureValues
}

/* ------------------------------------------------------------------ payloads */

/**
 * Creates a learning goal.
 *
 * `title` is the only required field. `target_skill_id` and `target_topic` are
 * both optional because a goal can name a subject that has no skill row yet —
 * the two are alternative forms of the same idea, and requiring both would force
 * a user to create a skill before they could write down what they want to learn.
 *
 * `progress`, `status`, `priority` and `estimated_effort_minutes` are all the
 * user's own claims and default server-side. A form that wants to set a level of
 * ambition must do it explicitly here rather than have NEXUS infer one.
 */
export interface LearningGoalCreatePayload {
  title: string
  description?: string | null
  target_skill_id?: UUIDString | null
  target_topic?: string | null
  target_date?: DateOnlyString | null
  priority?: ProjectPriority
  status?: LearningGoalStatus
  progress?: number
  estimated_effort_minutes?: number | null
  project_id?: UUIDString | null
  note_id?: UUIDString | null
}

/**
 * Edits a learning goal.
 *
 * Every field is optional and nullable: an explicit `null` clears the stored
 * value, while omitting the key leaves it untouched.
 *
 * `completed_at` is deliberately absent. Completion is a route
 * (`POST /learning/goals/{goal_id}/complete`) and not an editable field,
 * because a timestamp the user typed in is not the same record as a completion
 * the backend stamped, and the second is what `LEARNING_GOAL_COMPLETED` was
 * emitted for.
 */
export interface LearningGoalUpdatePayload {
  title?: string
  description?: string | null
  target_skill_id?: UUIDString | null
  target_topic?: string | null
  target_date?: DateOnlyString | null
  priority?: ProjectPriority
  status?: LearningGoalStatus
  progress?: number
  estimated_effort_minutes?: number | null
  project_id?: UUIDString | null
  note_id?: UUIDString | null
}

/**
 * Creates a skill with a level the user set.
 *
 * `name` is the only required field. `current_level` and `target_level`
 * default to the schema's own defaults and `level_source` to `user_defined`, so
 * **a skill created through a form is always attributed to the person** unless
 * the form deliberately sets `system_estimate` — which it should not, because
 * an estimate is the backend's to make and only once
 * `LEARNING_MIN_EVIDENCE_FOR_ESTIMATE` activities exist.
 *
 * `confidence` is absent for the same reason it is not editable later: it is
 * computed from evidence, and a client that could set it could forge the one
 * number that lends an estimate its credibility.
 */
export interface SkillCreatePayload {
  name: string
  category?: string | null
  description?: string | null
  /** 1–5, the user's own claim. */
  current_level?: number
  /** 1–5, where the user wants to be. */
  target_level?: number
  /** Defaults to `user_defined` server-side. */
  level_source?: SkillLevelSource
}

/**
 * Edits a skill.
 *
 * `level_source` is editable, and moving a skill from `user_defined` to
 * `system_estimate` is the honest way for the backend to take a level over —
 * it re-derives the level and the confidence from recorded evidence rather than
 * keeping the old number under a new label.
 *
 * `evidence_count`, `confidence` and `last_activity_at` are absent for the
 * mirror-image reason: they are measured, not typed. What you may edit is what
 * you typed.
 */
export interface SkillUpdatePayload {
  name?: string
  category?: string | null
  description?: string | null
  current_level?: number
  target_level?: number
  level_source?: SkillLevelSource
}

/**
 * Records one learning activity, with its evidence trail.
 *
 * `title` and `activity_type` are required; everything else may be absent and
 * the backend defaults it. `duration_minutes` is omitted for an event rather
 * than a span — sending `0` for "it was instantaneous" would claim a measured
 * zero-length session.
 *
 * `source_type`/`source_id` are the traceable half of rule 4: an activity that
 * came from somewhere names it, and one the user typed in leaves both null. No
 * field here claims the activity *completed* anything — `activity_type` names the
 * kind of event, which is the whole claim.
 */
export interface LearningActivityCreatePayload {
  title: string
  activity_type: LearningActivityType
  skill_id?: UUIDString | null
  goal_id?: UUIDString | null
  description?: string | null
  /** Defaults to now server-side when omitted. */
  occurred_at?: ISODateTimeString
  duration_minutes?: number | null
  source_type?: string | null
  source_id?: UUIDString | null
}

/**
 * `PUT /career/profile` — creates or replaces the caller's profile.
 *
 * Every field is optional and nullable: an explicit `null` clears the stored
 * value. `links` replaces the whole list rather than appending, which is what a
 * `PUT` means and what makes the call idempotent — sending the same body twice
 * must produce the same profile.
 *
 * **Nothing here is generated.** There is no field for a summary NEXUS wrote, a
 * target role it inferred from skills, or a highlight it chose. A client that
 * wanted to prefill the form from the profile it just fetched may, but what it
 * sends is still the user's.
 */
export interface CareerProfileUpsert {
  target_role?: string | null
  target_domain?: string | null
  headline?: string | null
  summary?: string | null
  location?: string | null
  links?: string[]
}

/**
 * Adds one dated record to the profile.
 *
 * `kind` and `title` are required; every date and every organisation is the
 * user's to supply and may be omitted. Nothing is inferred from the kind — a
 * `certification` with no issuer and no date is a valid row that says only what
 * the user said.
 */
export interface CareerExperienceCreatePayload {
  kind: CareerRecordKind
  title: string
  organisation?: string | null
  started_on?: DateOnlyString | null
  /** Omit for something current. */
  ended_on?: DateOnlyString | null
  description?: string | null
  url?: string | null
}

/**
 * Edits one dated record.
 *
 * Every field is optional and nullable; `kind` is included because moving a row
 * from `experience` to `education` is a correction the user is entitled to make.
 * The backend refuses a date pair that runs backwards.
 */
export interface CareerExperienceUpdatePayload {
  kind?: CareerRecordKind
  title?: string
  organisation?: string | null
  started_on?: DateOnlyString | null
  ended_on?: DateOnlyString | null
  description?: string | null
  url?: string | null
}

/**
 * Adds one piece of career evidence.
 *
 * `evidence_type`, `title` and `occurred_on` are required — evidence with no
 * date could not be ordered, so the backend will not store one. `source` defaults
 * to `manual`, which is the correct value for anything the user typed and the
 * only one a client should send by hand: a client that set `source` to a
 * subsystem name would be claiming a derivation it did not perform, which is
 * the same forgery as inventing the qualification itself.
 *
 * The three nullable links are what let a row be attached to the project, skill
 * or repository it came from. All three may be null.
 */
export interface CareerEvidenceCreatePayload {
  evidence_type: CareerEvidenceType
  title: string
  occurred_on: DateOnlyString
  description?: string | null
  project_id?: UUIDString | null
  skill_id?: UUIDString | null
  repository_id?: UUIDString | null
}

/**
 * Edits one piece of evidence.
 *
 * Every field is optional and nullable. `created_at` is absent, and with it the
 * ability to backdate the claim's *origination*: `occurred_on` may move, but
 * NEXUS keeps the record of when the row entered the profile.
 */
export interface CareerEvidenceUpdatePayload {
  evidence_type?: CareerEvidenceType
  title?: string
  occurred_on?: DateOnlyString
  description?: string | null
  project_id?: UUIDString | null
  skill_id?: UUIDString | null
  repository_id?: UUIDString | null
}