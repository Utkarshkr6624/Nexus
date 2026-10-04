/**
 * Wire types for the Phase 7 risk detection and recommendation surface.
 *
 * Mirrors `backend/app/schemas/risk.py` and `backend/app/schemas/recommendation.py`.
 *
 * Two disciplines from the Phase 6 types carry over unchanged, because the
 * reasoning behind them was settled there:
 *
 * - **The backend emits `null`, never an absent key.** Every nullable field here
 *   is `T | null` and required, not `field?: T`. `resolved_at: string | null`
 *   distinguishes "this risk is still open" from "this response shape has no
 *   `resolved_at` key", and only the first of those is a claim a screen may make.
 *   An optional property would collapse the two and let a `?? '—'` fallback
 *   quietly invent a value for a field the server chose to leave out.
 * - **A figure that could not be computed is `null`, never `0`.** It matters less
 *   on this surface than it did on analytics, and for an instructive reason: a
 *   risk row only ever exists for a condition the engine *could* judge, because
 *   an unavailable detector is dropped before it is persisted. So `RiskRead.score`
 *   is a plain `number` — there is no cold-start row to render — while the run
 *   summary does carry `null` (`EvaluationRead.reason_if_not_evaluated`) for the
 *   passes that judged nothing. The distinction is worth keeping explicit, because
 *   a risk score of 0 and a risk that could not be assessed are different facts
 *   and only one of them can ever reach this type.
 *
 * The vocabulary below is a closed set mirroring `app/models/enums.py`, in the
 * enum's own declaration order rather than any order a screen might want. Each
 * list's order carries meaning of its own and is documented where it matters —
 * `RISK_SEVERITIES` is sorted most-severe-first because the backend sorts the
 * Risk Center the same way, so a client that sorts the array and a query the
 * server answers cannot disagree.
 */

import type { ISODateTimeString, UUIDString } from './api'
import type { PaginationParams } from './pagination'

// Re-exported so a consumer of the risk vocabulary needs one import, not three.
export type { ISODateTimeString, PaginationParams, UUIDString }

/* -------------------------------------------------------------- vocabulary */

/**
 * What kind of condition a risk describes — the seven detectors, and nothing
 * else.
 *
 * In the backend's declaration order, which is deadline, workload, project, task,
 * scheduling, estimation, consistency. A closed set on purpose: a risk row can
 * only name a condition some detector knows how to re-derive, which is what
 * makes "the condition disappeared, so resolve the risk" a decidable question
 * rather than a guess.
 */
export const RISK_TYPES = [
  'deadline',
  'workload',
  'project',
  'task',
  'scheduling',
  'estimation',
  'consistency',
] as const
export type RiskType = (typeof RISK_TYPES)[number]

/**
 * How loudly a risk speaks. Ordered **most severe first**, deliberately.
 *
 * The order is the contract: the backend sorts `GET /risks` by severity
 * descending, so a plain sort over this array reproduces the server's ordering
 * exactly. Severity is also always *derived* from the score server-side, never
 * chosen by a client, so this union is a read vocabulary rather than a write one.
 */
export const RISK_SEVERITIES = ['critical', 'high', 'medium', 'low'] as const
export type RiskSeverity = (typeof RISK_SEVERITIES)[number]

/**
 * Where a risk sits in its lifecycle.
 *
 * `active` and `acknowledged` are the two states a risk can be re-detected into
 * without creating a second row, and they are what the backend's partial unique
 * index keys on. `acknowledged` is not `resolved`: it says "still true, and I
 * have seen it", which is where a risk the user intends to live with rests so it
 * stops competing for attention.
 */
export const RISK_STATUSES = ['active', 'acknowledged', 'resolved', 'dismissed'] as const
export type RiskStatus = (typeof RISK_STATUSES)[number]

/**
 * What kind of action a recommendation proposes.
 *
 * Every member names something a person does, and none of them names something
 * NEXUS does — the engine proposes, it never performs. That constraint is
 * structural rather than editorial here: there is no member a client could
 * attach to "reschedule this for me".
 */
export const RECOMMENDATION_TYPES = [
  'reschedule_task',
  'break_down_task',
  'reduce_workload',
  'start_task',
  'prioritize_task',
  'review_deadline',
  'update_estimate',
  'block_time',
  'complete_blocked_task',
  'review_project',
  // Phase 9. Both name something the person does with their own learning record,
  // and both are raised without a risk behind them — hence `risk_id: null`.
  'review_learning_goal',
  'revive_target_skill',
] as const
export type RecommendationType = (typeof RECOMMENDATION_TYPES)[number]

/**
 * What has happened to a recommendation since it was raised.
 *
 * `new` and `viewed` are the open states a recommendation can be re-raised into;
 * a rejected suggestion becomes raisable again because the user declining once
 * does not mean the underlying condition changed. `expired` is set by the
 * service rather than by a clock — the risk behind the suggestion was resolved,
 * so it is moot, which is a more useful thing to record than "old".
 */
export const RECOMMENDATION_STATUSES = [
  'new',
  'viewed',
  'accepted',
  'rejected',
  'completed',
  'expired',
] as const
export type RecommendationStatus = (typeof RECOMMENDATION_STATUSES)[number]

/** The open states, i.e. what a "still to answer" filter offers. */
export const OPEN_RECOMMENDATION_STATUSES: readonly RecommendationStatus[] = ['new', 'viewed']

/**
 * How soon a recommendation wants an answer.
 *
 * The same four grades as {@link RiskSeverity}, and derived from the same
 * score: priority and severity are two views of one number rather than two
 * opinions, which is why there is no way to send one without the other.
 */
export const RECOMMENDATION_PRIORITIES = ['critical', 'high', 'medium', 'low'] as const
export type RecommendationPriority = (typeof RECOMMENDATION_PRIORITIES)[number]

/**
 * How much data a finding was derived from.
 *
 * **This is not confidence.** Nothing on this surface is a probability or a
 * fitted parameter; the field reports how many observations the rule had to work
 * with. Keeping it out of the word "confidence" is the point, and a UI that
 * renders it as a percentage would reintroduce the misrepresentation the name
 * exists to prevent. It is the cold-start signal: a thin sample is still
 * reported, but never as though it were firm.
 */
export const EVIDENCE_STRENGTHS = ['high', 'medium', 'low'] as const
export type EvidenceStrength = (typeof EVIDENCE_STRENGTHS)[number]

/* --------------------------------------------------------------- list params */

/**
 * Query parameters for `GET /risks`.
 *
 * The filters are single-valued rather than arrays even though the repository
 * layer accepts sets: `api-client`'s `QueryParams` carries one scalar per key and
 * its serialiser cannot emit a repeated key, so an array here would be dropped
 * silently and the screen would show a wider result set than the user asked for.
 * One value per filter is the most this client can honestly promise.
 */
export interface RiskListParams extends PaginationParams {
  status?: RiskStatus
  risk_type?: RiskType
  /** Narrow to one band. Served from the third column of
   *  `ix_risks_owner_status_severity`, and `by_severity`/`total` describe the
   *  filtered set rather than one page of it. */
  severity?: RiskSeverity
}

/** Query parameters for `GET /recommendations`. Same single-valued rule. */
export interface RecommendationListParams extends PaginationParams {
  status?: RecommendationStatus
  recommendation_type?: RecommendationType
}

/**
 * Body-free parameters for `POST /intelligence/evaluate`.
 *
 * The window defaults server-side, so an omitted `window_days` asks for the
 * engine's own default rather than for a window this module would have invented.
 */
export interface EvaluationParams {
  window_days?: number
}

/* -------------------------------------------------------------------- shapes */

/**
 * One line of a score's breakdown, so the parts can be checked against the
 * total rather than taken on trust. This is the "why" the brief requires, and a
 * risk with a score and no evidence is not constructible through the service.
 */
export interface RiskEvidenceRead {
  label: string
  detail: string
  /** How much this input moved the score. Signed, so it can also be read
   * against the total rather than merely added to it. */
  contribution: number
}

/**
 * A recommendation as it appears nested inside a risk.
 *
 * A projection, not a smaller copy: it drops `description`, `entity_id`,
 * `responded_at` and `metadata`, because the parent risk has already said which
 * entity is involved and the card has no room for the rest.
 *
 * It **keeps `reason`**, and that is the load-bearing decision. The full model
 * rejects a blank reason, so a projection that dropped it would put a bare
 * imperative one level below the card where the brief rules it out — which is
 * exactly the screen a user reads while deciding what to do.
 */
export interface RecommendationSummaryRead {
  id: UUIDString
  recommendation_type: RecommendationType
  priority: RecommendationPriority
  /** WHAT is being asked for, in a few words. */
  title: string
  /** WHY this was proposed. Kept here for the reason above. */
  reason: string
  status: RecommendationStatus
  created_at: ISODateTimeString
}

/**
 * One live condition the detection engine found, and why it thinks so.
 *
 * `score` is 0-100 and `severity` is derived from it server-side, so the two
 * cannot disagree. `evidence_strength` and `evidence` travel together because
 * either alone is misleading: the lines say how the number was reached, the
 * strength says how much data it was reached from.
 */
export interface RiskRead {
  id: UUIDString
  risk_type: RiskType
  severity: RiskSeverity
  /** Always a real score. An unavailable detector never produces a row, so a
   * `null` here would mean the schema changed rather than that nothing was
   * found. */
  score: number
  title: string
  /** Neutral and factual by brief: what is true about the data, never what it
   * implies about the person. */
  description: string
  evidence: RiskEvidenceRead[]
  evidence_strength: EvidenceStrength
  /** `task` / `project` / `account`, or `null` for a finding about the account
   * as a whole — which is how a workload risk and a consistency risk can be
   * live at once without colliding on the deduplication index. */
  entity_type: string | null
  entity_id: UUIDString | null
  status: RiskStatus
  detected_at: ISODateTimeString
  /** Null for a live risk, set for `resolved`/`dismissed`. That difference is
   * what makes "how long was this open" answerable without the event log. */
  resolved_at: ISODateTimeString | null
  /** The raw inputs the score was computed from, so a stored risk stays
   * auditable after the aggregates it was derived from have been rebuilt. */
  metadata: Record<string, unknown>
  /** Empty when no suggestion has been raised — a real state the Risk Center
   * renders as "No suggested action yet", not as an error or a loading gap. */
  recommendations: RecommendationSummaryRead[]
}

/**
 * The Risk Center list envelope.
 *
 * `total`/`limit`/`offset` sit at the top level rather than under a `meta` key
 * as they do for the `Paginated[T]` endpoints, because this envelope also
 * carries `by_severity` and `summary`, which have no place in a generic page
 * wrapper.
 */
export interface RiskListRead {
  items: RiskRead[]
  total: number
  limit: number
  offset: number
  /**
   * Counts keyed by severity across every matching risk, not just this page.
   *
   * Always carries all four bands, zeroed where nothing was found, so the shape
   * does not change as the last critical risk is resolved — and so no caller
   * needs a fallback default that would make an absent band and an empty one the
   * same number.
   */
  by_severity: Record<string, number>
  /** One sentence the backend composed, so the header wording stays consistent
   * across the Risk Center and the recommendation list. */
  summary: string
}

/**
 * Compact counts for the dashboard widget.
 *
 * Separate from {@link RiskListRead} because the dashboard asks a different
 * question — "does anything need me?" — and does not want a page of risks to
 * answer it. `needs_attention` is derived server-side so the widget and the Risk
 * Center cannot disagree about what counts as urgent.
 */
export interface RiskSummaryRead {
  critical: number
  high: number
  medium: number
  low: number
  total: number
  /** True when at least one live risk is `high` or `critical`. Medium and low
   * are counted but do not raise it: a widget that alarms over an amber band
   * teaches people to ignore it. This is the one bit the widget leads with; the
   * counts above are for the detail view. */
  needs_attention: boolean
}

/**
 * A proposed action, its reason, and what the user did about it.
 *
 * `reason` is why the suggestion exists, in words and numbers; `description` is
 * the action being proposed. Neither is optional and the backend makes an empty
 * `reason` unconstructible, so a client never has to render a bare imperative
 * with nothing behind it.
 */
export interface RecommendationRead {
  id: UUIDString
  recommendation_type: RecommendationType
  priority: RecommendationPriority
  /** WHAT is being asked for, in the imperative. */
  title: string
  /** The suggested action in full. */
  description: string
  /** WHY, with the numbers that produced it. */
  reason: string
  entity_type: string | null
  entity_id: UUIDString | null
  /** The risk that raised this, or `null` when the rule fired without one —
   * and also once the risk has been deleted, because deleting a risk must not
   * delete the record of having acted on it. */
  risk_id: UUIDString | null
  status: RecommendationStatus
  created_at: ISODateTimeString
  /** Null until the user acts, which is how "how many were never answered" is
   * answered without a second query. */
  responded_at: ISODateTimeString | null
  /** Set when the risk behind the suggestion was resolved. */
  expires_at: ISODateTimeString | null
  metadata: Record<string, unknown>
}

/**
 * The recommendations list envelope.
 *
 * Same top-level paging as {@link RiskListRead} and for the same reason: the
 * header tally and its sentence belong next to the rows on screen, not buried in
 * a `meta` object whose other members are pagination bookkeeping.
 */
export interface RecommendationListRead {
  items: RecommendationRead[]
  total: number
  limit: number
  offset: number
  /** Counts keyed by priority across every matching row, not just this page.
   * Filled with all four priority words, zeroed where nothing was found, so the
   * shape does not change as the last critical recommendation is closed. */
  by_priority: Record<string, number>
}

/**
 * One detection run, summarised.
 *
 * `evaluated: false` with a reason is a normal answer, not a failure: a run over
 * an empty or too-new account has nothing to judge, and saying so is more
 * useful than reporting zeros that read as "you have no risks". The counts are
 * a snapshot of one moment; the risks themselves are the durable record.
 */
export interface EvaluationRead {
  evaluated: boolean
  /** Why the pass did not run, or `null` when it did. Paired with
   * `evaluated: false` by convention rather than by an enforced invariant — a
   * response model that raised on a service's mistake would surface it as a 500
   * instead of as the sentence the user needed. */
  reason_if_not_evaluated: string | null
  evaluated_at: ISODateTimeString
  /** The window the run reasoned over, so the snapshot is interpretable without
   * also reconstructing which range produced it. */
  window_start: ISODateTimeString
  window_end: ISODateTimeString
  /** Live risks after the pass. Not created plus updated: the run also resolved
   * live risks it did not re-detect, so these counts deliberately do not sum to
   * this one. */
  risks_found: number
  /** New rows written, each of which raised a `risk_detected` event. This is the
   * number that answers "is the engine finding new things, or only re-reporting
   * the same ones". */
  risks_created: number
  /** Existing live rows refreshed in place, which is the deduplication working:
   * the same condition found again is updated rather than duplicated. */
  risks_updated: number
  /** Live risks the pass did not re-detect and transitioned to resolved,
   * because the condition behind them is gone. */
  risks_resolved: number
  /** Counts by severity and by risk type across the risks this run touched.
   * Both are empty when the run touched none — unlike the two list envelopes,
   * which zero-fill their bands, because "no run, no breakdown" is the honest
   * answer here rather than four zeroes that read as a clean bill of health. */
  by_severity: Record<string, number>
  by_type: Record<string, number>
  /** Actions this run proposed. Zero is normal: a rule fires at most once per
   * entity, and most runs re-find a risk whose suggestion already exists. */
  recommendations_created: number
  /** Milliseconds the pass took. Cheap to record and the only way to notice
   * that a change made evaluation expensive. */
  duration_ms: number
}

/** Query parameters for `GET /intelligence/evaluations`: how many runs to read. */
export interface EvaluationListParams {
  limit?: number
}