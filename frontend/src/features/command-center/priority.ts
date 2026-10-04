/**
 * The Command Center's priority rule: one deterministic, explained ranking over
 * signals gathered from real endpoints.
 *
 * ## What this file is not
 *
 * It is not a model. NEXUS runs one model — `microsoft/deberta-v3-base`, a
 * fourteen-class intent classifier — and that model maps a sentence to an intent
 * name. It never sees a task, a risk, a conflict or a deadline, and nothing in
 * this file calls it. Everything below is arithmetic over numbers the backend
 * already sent, so the same inputs always produce the same order.
 *
 * ## The rule
 *
 * Each signal scores up to six factors, and **a signal only scores the factors
 * that apply to it**. A planner conflict has no deadline and no task priority, so
 * its ceiling is smaller than a task's. The raw total is normalised against that
 * signal's *own* ceiling:
 *
 * ```
 * priority = round(100 × raw ÷ ceiling)
 * ```
 *
 * Normalising per signal rather than per factor is what lets a risk and a
 * scheduling conflict share one ordered list without either being scaled by
 * hand, and every {@link PriorityFactor} carries the ceiling it was measured
 * against so the arithmetic is visible on the row itself.
 *
 * The factors:
 *
 * | Factor | Applies to | Points | Ceiling |
 * | --- | --- | --- | --- |
 * | Severity | every signal | 0–40 | 40 |
 * | Deadline proximity | any signal with a date | 0–25 | 25 |
 * | Task / goal priority | tasks, conflicts, goals | 0–15 | 15 |
 * | Detector score | risk findings | 0–10 | 10 |
 * | Suggestion priority | suggestions | 0–10 | 10 |
 * | Freshness | risks and suggestions | 0–15 | 15 |
 *
 * ## Why these buckets, stated plainly
 *
 * Every threshold below is a **convention chosen here**, not a measurement. The
 * backend owns the severity *bands* (`risk_severity_for`) and the recommendation
 * priorities, but it does not say how many points a band is worth when ranking
 * across modules — that is a product decision, and this file is where it is
 * recorded. {@link PRIORITY_CAVEATS} carries the same sentences to the UI, so a
 * reader is told which parts of the number were observed and which were chosen.
 *
 * ## Determinism
 *
 * Ordering is: priority descending, then band rank, then `id` ascending with a
 * plain `<` comparison (never `localeCompare`, which is locale-dependent). Two
 * runs over the same signals therefore produce byte-identical output regardless
 * of the order the endpoints returned them in — asserted in
 * `command-center-page.test.tsx`.
 */

import type { Conflict } from '@/types/planner'
import type { LearningGoalRead } from '@/types/learning'
import type {
  RecommendationRead,
  RecommendationPriority,
  RiskRead,
  RiskSeverity,
} from '@/types/risk'
import type { DateOnlyString, Task, TaskPriority } from '@/types/work'

/**
 * Where a figure came from. These are three different kinds of thing and the UI
 * says which, because a count of rows in a table and a score this file just
 * computed are not interchangeable evidence.
 *
 * - `measured` — counted from stored records by a real endpoint.
 * - `calculated` — a deterministic score computed here, in the browser, from
 *   measured inputs. Never a probability and never a statement about a person.
 * - `model-derived` — produced by the intent classifier. The Command Center
 *   never ranks anything with it; that label appears only where a prediction
 *   exists, and it always does.
 */
export type Provenance = 'measured' | 'calculated' | 'model-derived'

/** The kinds of thing that can be waiting on a decision. */
export type SignalKind =
  | 'risk'
  | 'recommendation'
  | 'task-deadline'
  | 'task-priority'
  | 'planner-conflict'
  | 'learning-goal'

export const SIGNAL_KIND_LABEL: Record<SignalKind, string> = {
  risk: 'Risk finding',
  recommendation: 'Suggestion',
  'task-deadline': 'Deadline',
  'task-priority': 'Critical task',
  'planner-conflict': 'Schedule conflict',
  'learning-goal': 'Learning goal',
}

/** One line of the score, so a number on screen can be argued with. */
export interface PriorityFactor {
  label: string
  points: number
  /** The largest this factor could contribute to *this* signal. */
  ceiling: number
  /** The sentence that says why this many points and not another. */
  why: string
}

export interface PrioritySignal {
  /** Stable, kind-scoped, and unique across kinds. Doubles as the React key. */
  id: string
  kind: SignalKind
  title: string
  /** One factual sentence about the record. Never an instruction to the reader. */
  detail: string
  band: RiskSeverity
  /** 0–100 after normalisation. Deterministic. */
  priority: number
  /** Un-normalised total, and the ceiling it was measured against. */
  raw: number
  ceiling: number
  factors: PriorityFactor[]
  href: string
  /** Always `calculated`: the *record* is measured, the ordering is not. */
  provenance: Provenance
  /** `YYYY-MM-DD` when the record carries a deadline the rule could read. */
  dueOn: DateOnlyString | null
}

/* ------------------------------------------------------------------ weights */

/**
 * Severity band → points. The band is the backend's; the weighting is this
 * file's, and {@link PRIORITY_CAVEATS} says so out loud.
 */
export const SEVERITY_POINTS: Record<RiskSeverity, number> = {
  critical: 40,
  high: 30,
  medium: 18,
  low: 8,
}

/** Task priority → points. Same shape as {@link SEVERITY_POINTS}. */
export const PRIORITY_POINTS: Record<TaskPriority, number> = {
  critical: 15,
  high: 11,
  medium: 6,
  low: 2,
}

/**
 * Suggestion priority → points. A suggestion is advice about a finding, so it
 * scores its own priority band and inherits the finding's severity points above;
 * the two are two views of one number server-side, never two opinions.
 */
export const RECOMMENDATION_POINTS: Record<RecommendationPriority, number> = {
  critical: 10,
  high: 7,
  medium: 4,
  low: 2,
}

const CEILING_SEVERITY = 40
const CEILING_DEADLINE = 25
const CEILING_ITEM_PRIORITY = 15
const CEILING_DETECTOR_SCORE = 10
const CEILING_RECOMMENDATION = 10
const CEILING_FRESHNESS = 15

/**
 * The rule, as rows the UI renders. Kept beside the arithmetic it describes so
 * the two cannot drift: a factor applied below without a row here would be a
 * number nobody could check.
 */
export const PRIORITY_RULE: readonly { label: string; applies: string; detail: string }[] = [
  {
    label: 'Severity',
    applies: 'Every signal',
    detail: 'The band the backend assigned: critical 40, high 30, medium 18, low 8.',
  },
  {
    label: 'Deadline proximity',
    applies: 'Any signal with a date',
    detail:
      'Overdue by 7 days or more 25; by 3 or more 20; by 1 or more 16; due today 12; ' +
      'within 3 days 8; within 7 days 4; later or undated 0.',
  },
  {
    label: 'Task / goal priority',
    applies: 'Tasks, schedule conflicts and goals',
    detail: 'The priority you set: critical 15, high 11, medium 6, low 2.',
  },
  {
    label: 'Detector score',
    applies: 'Risk findings',
    detail: 'The detection engine’s own 0–100 score, divided by ten.',
  },
  {
    label: 'Suggestion priority',
    applies: 'Suggestions',
    detail: 'The priority the backend derived from the finding behind it.',
  },
  {
    label: 'Freshness',
    applies: 'Risks and suggestions',
    detail:
      'Seen within 3 days 15; 7 days 12; 14 days 9; 30 days 5; older 2. A finding the ' +
      'engine has not re-found recently may no longer hold.',
  },
]

/**
 * What in the rule was **chosen** rather than measured.
 *
 * Rendered verbatim under the ranked list. These three sentences are the whole
 * of the fuzziness: the buckets are conventions, the freshness assumption is a
 * judgement, and the normalisation means two raw totals from different kinds of
 * signal are not directly comparable.
 */
export const PRIORITY_CAVEATS: readonly string[] = [
  'The point values above are a convention chosen for this page, not a measurement. ' +
    'The severity bands themselves come from the backend.',
  'Deadline buckets are round numbers chosen for legibility. Two deadlines a few days ' +
    'apart can score the same, and that is intended.',
  'Freshness assumes a finding the detection engine has not re-found recently may no ' +
    'longer hold. It is a judgement about staleness, not an observation.',
]

/* -------------------------------------------------------------------- dates */

/** Whole days from `now` to `date`. Date-only, so it is compared in UTC. */
export function daysUntil(now: Date, date: DateOnlyString): number {
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  const [year, month, day] = date.split('-').map(Number)
  const target = Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1)
  if (Number.isNaN(target)) return Number.POSITIVE_INFINITY
  return Math.round((target - today) / 86_400_000)
}

/** `YYYY-MM-DD` for the day `now` falls on, in UTC. */
export function dateOnlyOf(now: Date): DateOnlyString {
  return now.toISOString().slice(0, 10)
}

/** `YYYY-MM-DD` for `now` shifted by whole days. */
export function shiftDateOnly(now: Date, days: number): DateOnlyString {
  return new Date(now.getTime() + days * 86_400_000).toISOString().slice(0, 10)
}

function plural(days: number): string {
  return days === 1 ? '1 day' : `${days} days`
}

/** Proximity points and the sentence explaining the bucket that was chosen. */
function deadlineFactor(now: Date, date: DateOnlyString | null): PriorityFactor | null {
  if (!date) return null
  const days = daysUntil(now, date)
  const base = { label: 'Deadline proximity', ceiling: CEILING_DEADLINE }

  if (days < -7) {
    return { ...base, points: 25, why: `Due ${date} — ${plural(Math.abs(days))} ago.` }
  }
  if (days < -3) return { ...base, points: 20, why: `Due ${date} — ${plural(Math.abs(days))} ago.` }
  if (days < 0) return { ...base, points: 16, why: `Due ${date} — ${plural(Math.abs(days))} ago.` }
  if (days === 0) return { ...base, points: 12, why: `Due today (${date}).` }
  if (days <= 3) return { ...base, points: 8, why: `Due in ${plural(days)}, on ${date}.` }
  if (days <= 7) return { ...base, points: 4, why: `Due in ${plural(days)}, on ${date}.` }
  return { ...base, points: 0, why: `Due ${date} — more than a week away.` }
}

/** Whole days since an ISO instant, against the injected clock. */
function daysSince(now: Date, instant: string | null): number | null {
  if (!instant) return null
  const then = Date.parse(instant)
  if (Number.isNaN(then)) return null
  return Math.floor((now.getTime() - then) / 86_400_000)
}

function freshnessFactor(now: Date, instant: string | null): PriorityFactor {
  const days = daysSince(now, instant)
  if (days === null) {
    return { label: 'Freshness', points: 0, ceiling: CEILING_FRESHNESS, why: 'No timestamp to measure age from.' }
  }
  const points = days <= 3 ? 15 : days <= 7 ? 12 : days <= 14 ? 9 : days <= 30 ? 5 : 2
  return { label: 'Freshness', points, ceiling: CEILING_FRESHNESS, why: `Raised ${plural(days)} ago.` }
}

/* --------------------------------------------------------------- band order */

/** Most severe first. Used as the first tie-break, never as a re-sort of bands. */
const BAND_RANK: Record<RiskSeverity, number> = { critical: 0, high: 1, medium: 2, low: 3 }

/**
 * Normalises a band word this build does not know.
 *
 * A payload from a newer engine can carry a band the client was never taught.
 * Falling back to `low` keeps the row visible and keeps the ceiling honest,
 * rather than dropping a finding the user would otherwise never see.
 */
function bandOf(value: string): RiskSeverity {
  return value in SEVERITY_POINTS ? (value as RiskSeverity) : 'low'
}

/* ------------------------------------------------------------- normalisation */

function normalise(raw: number, ceiling: number): number {
  if (ceiling <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((100 * raw) / ceiling)))
}

function severityFactor(band: RiskSeverity): PriorityFactor {
  return {
    label: 'Severity',
    points: SEVERITY_POINTS[band],
    ceiling: CEILING_SEVERITY,
    why: `Band: ${band}.`,
  }
}

function itemPriorityFactor(band: RiskSeverity, source: string): PriorityFactor {
  return {
    label: 'Task / goal priority',
    points: PRIORITY_POINTS[band as TaskPriority] ?? 0,
    ceiling: CEILING_ITEM_PRIORITY,
    why: `${source} priority: ${band}.`,
  }
}

/** Assembles one signal from the factors that actually apply to it. */
function buildSignal(input: {
  id: string
  kind: SignalKind
  title: string
  detail: string
  band: RiskSeverity
  href: string
  dueOn: DateOnlyString | null
  factors: (PriorityFactor | null)[]
}): PrioritySignal {
  const factors = input.factors.filter((factor): factor is PriorityFactor => factor !== null)
  const raw = factors.reduce((total, factor) => total + factor.points, 0)
  const ceiling = factors.reduce((total, factor) => total + factor.ceiling, 0)

  return {
    id: input.id,
    kind: input.kind,
    title: input.title,
    detail: input.detail,
    band: input.band,
    priority: normalise(raw, ceiling),
    raw,
    ceiling,
    factors,
    href: input.href,
    provenance: 'calculated',
    dueOn: input.dueOn,
  }
}

/**
 * The single ordering function. Exported so a test can shuffle its input and
 * assert the output is unchanged — the claim the rule makes about itself.
 */
export function rankSignals(signals: readonly PrioritySignal[]): PrioritySignal[] {
  return [...signals].sort((a, b) => {
    if (a.priority !== b.priority) return b.priority - a.priority
    if (BAND_RANK[a.band] !== BAND_RANK[b.band]) return BAND_RANK[a.band] - BAND_RANK[b.band]
    // Plain `<` on purpose: `localeCompare` depends on the runtime's locale and
    // would make the order differ between a developer's machine and CI.
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
  })
}

/* ------------------------------------------------------------- the collectors */

export interface SignalSources {
  /** Live findings from `GET /risks?status=active`. */
  risks: readonly RiskRead[]
  /** Unanswered suggestions from `GET /recommendations?status=new`. */
  recommendations: readonly RecommendationRead[]
  /** Open tasks due on or before today, from `GET /tasks?due_before`. */
  deadlines: readonly Task[]
  /** Open critical tasks, from `GET /tasks?priority=critical`. */
  criticalTasks: readonly Task[]
  /** Conflicts over the next fortnight, from `GET /planner/conflicts`. */
  conflicts: readonly Conflict[]
  /** Active goals, from `GET /learning/goals?status=active`. */
  learningGoals: readonly LearningGoalRead[]
}

function riskSignals(risks: readonly RiskRead[], now: Date): PrioritySignal[] {
  return risks.map((risk) => {
    const band = bandOf(risk.severity)
    return buildSignal({
      id: `risk:${risk.id}`,
      kind: 'risk',
      title: risk.title,
      detail: risk.description,
      band,
      href: '/risks',
      // A finding carries no deadline of its own. It is left off rather than
      // scored at zero, which is what keeps its ceiling smaller than a task's.
      dueOn: null,
      factors: [
        severityFactor(band),
        {
          label: 'Detector score',
          points: Math.round(risk.score / 10),
          ceiling: CEILING_DETECTOR_SCORE,
          why: `Detection score ${risk.score} of 100.`,
        },
        freshnessFactor(now, risk.detected_at),
      ],
    })
  })
}

function recommendationSignals(
  recommendations: readonly RecommendationRead[],
  now: Date,
): PrioritySignal[] {
  return recommendations.map((row) => {
    const band = bandOf(row.priority)
    return buildSignal({
      id: `recommendation:${row.id}`,
      kind: 'recommendation',
      title: row.title,
      // The reason, not the description: a reader who disagrees with a
      // suggestion can only do so on the evidence, and the evidence is this.
      detail: row.reason,
      band,
      href: '/recommendations',
      dueOn: null,
      factors: [
        severityFactor(band),
        {
          label: 'Suggestion priority',
          points: RECOMMENDATION_POINTS[band],
          ceiling: CEILING_RECOMMENDATION,
          why: `Priority: ${row.priority}.`,
        },
        freshnessFactor(now, row.created_at),
      ],
    })
  })
}

function taskSignals(
  tasks: readonly Task[],
  kind: 'task-deadline' | 'task-priority',
  now: Date,
): PrioritySignal[] {
  return tasks.map((task) => {
    const band = bandOf(task.priority)
    return buildSignal({
      id: `${kind}:${task.id}`,
      kind,
      title: task.title,
      detail: task.is_overdue
        ? `Past its ${task.due_date ?? 'recorded'} due date and still open.`
        : `Open, due ${task.due_date ?? 'with no date recorded'}.`,
      band,
      href: '/tasks',
      dueOn: task.due_date,
      factors: [
        severityFactor(band),
        deadlineFactor(now, task.due_date),
        itemPriorityFactor(band, 'Task'),
      ],
    })
  })
}

function conflictSignals(conflicts: readonly Conflict[]): PrioritySignal[] {
  return conflicts.map((conflict, index) => {
    // A conflict carries `info` / `warning` / `error` — a three-value ladder,
    // not the four severity bands. It is mapped explicitly here rather than by
    // pretending the two vocabularies are the same thing.
    const band: RiskSeverity =
      conflict.severity === 'error' ? 'critical' : conflict.severity === 'warning' ? 'high' : 'medium'
    return buildSignal({
      // A conflict has no id of its own — `entity_id` is null for two intervals
      // that were both found — so the ordinal keeps the key unique and stable
      // within one response.
      id: `planner-conflict:${index}:${conflict.kind}`,
      kind: 'planner-conflict',
      title: conflict.kind.replace(/_/g, ' '),
      detail: conflict.message,
      band,
      href: '/planner',
      dueOn: null,
      factors: [
        severityFactor(band),
        {
          label: 'Task / goal priority',
          points: PRIORITY_POINTS[band as TaskPriority] ?? 0,
          ceiling: CEILING_ITEM_PRIORITY,
          why: `Planner severity: ${conflict.severity}.`,
        },
      ],
    })
  })
}

function learningGoalSignals(goals: readonly LearningGoalRead[], now: Date): PrioritySignal[] {
  return goals.map((goal) => {
    const band = bandOf(goal.priority)
    return buildSignal({
      id: `learning-goal:${goal.id}`,
      kind: 'learning-goal',
      title: goal.title,
      detail: goal.target_date
        ? `Target date ${goal.target_date}. Progress is the ${goal.progress}% you recorded.`
        : 'No target date recorded.',
      band,
      href: '/learning',
      dueOn: goal.target_date,
      factors: [
        severityFactor(band),
        deadlineFactor(now, goal.target_date),
        itemPriorityFactor(band, 'Goal'),
      ],
    })
  })
}

/**
 * Every signal the page knows how to build, ranked.
 *
 * `now` is injected rather than read from the clock inside, so a test can pin it
 * and a deadline bucket cannot move under an assertion between two runs.
 */
export function collectSignals(sources: SignalSources, now: Date): PrioritySignal[] {
  return rankSignals([
    ...riskSignals(sources.risks, now),
    ...recommendationSignals(sources.recommendations, now),
    ...taskSignals(sources.deadlines, 'task-deadline', now),
    ...taskSignals(sources.criticalTasks, 'task-priority', now),
    ...conflictSignals(sources.conflicts),
    ...learningGoalSignals(sources.learningGoals, now),
  ])
}
