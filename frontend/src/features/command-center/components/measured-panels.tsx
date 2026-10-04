/**
 * The measured panels: counts that came out of the database.
 *
 * **Every figure here is a count of records somebody created.** Nothing in this
 * file is a score, a rank or an estimate, which is why all three panels are
 * badged `Measured` and why they are visually separate from the calculated queue
 * beside them.
 *
 * **A figure the backend could not compute is a dash with its reason, never a
 * zero.** `OverviewRead` reports staleness with `is_stale` and the date the
 * aggregates were last written through, and that is printed rather than hidden:
 * a total that is three days old and a total that is current are different
 * claims, and the reader is entitled to the difference. `LearningSummaryRead`
 * and `DeveloperSummaryRead` both carry `has_data`, so an account with nothing
 * recorded renders its zero counts as an empty state instead of as a finding.
 */
import { formatMinutes, formatNumber, formatShortDate } from '@/features/analytics/format'
import type { OverviewRead } from '@/types/analytics'
import type { DeveloperSummaryRead } from '@/types/developer'
import type { LearningSummaryRead } from '@/types/learning'
import type { RiskSummaryRead } from '@/types/risk'

function Figure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-md border border-border p-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 font-mono text-2xl font-semibold tabular-nums text-foreground">{value}</p>
      {hint && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{hint}</p>}
    </div>
  )
}

/* --------------------------------------------------------------- analytics */

export function MomentumPanelBody({ overview }: { overview: OverviewRead }) {
  return (
    <div className="space-y-4">
      {overview.is_stale && (
        <p className="rounded-md border border-warning/40 bg-warning/[0.06] px-3 py-2 text-xs leading-relaxed text-foreground/85">
          These aggregates are stale
          {overview.aggregates_through
            ? ` — written through ${formatShortDate(overview.aggregates_through)}`
            : ''}
          . The records behind them are current; the rollup is not.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {overview.totals.slice(0, 6).map((total) => (
          <Figure
            key={total.label}
            label={total.label}
            value={formatNumber(total.current)}
            hint={
              total.previous === null
                ? 'No comparable previous window, so no change is shown.'
                : `Previously ${formatNumber(total.previous)} in the window before.`
            }
          />
        ))}
      </div>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Every figure is a count of rows the backend aggregated over{' '}
        {formatShortDate(overview.range.start_date)} to {formatShortDate(overview.range.end_date)}.
        {overview.reason_if_empty ? ` ${overview.reason_if_empty}` : ''}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------- risk */

export function FindingsPanelBody({ summary }: { summary: RiskSummaryRead }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Figure label="Critical" value={formatNumber(summary.critical)} />
        <Figure label="High" value={formatNumber(summary.high)} />
        <Figure label="Medium" value={formatNumber(summary.medium)} />
        <Figure label="Low" value={formatNumber(summary.low)} />
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {summary.needs_attention
          ? 'The backend raised needs-attention: at least one live finding is high or critical. Medium and low are counted but do not raise it.'
          : 'No live finding is high or critical. Medium and low are counted above and do not raise the flag.'}{' '}
        {formatNumber(summary.total)} finding{summary.total === 1 ? '' : 's'} live in total.
      </p>
    </div>
  )
}

/* -------------------------------------------------------------- developer */

export function EngineeringPanelBody({ summary }: { summary: DeveloperSummaryRead }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Figure
          label="Commits in window"
          value={formatNumber(summary.commits_in_window)}
          hint={`Whole history: ${formatNumber(summary.commit_count)}. Commits are timestamps, not hours.`}
        />
        <Figure
          label="Days with a commit"
          value={formatNumber(summary.active_days)}
          hint={`Distinct days carrying at least one commit in ${formatNumber(summary.window_days)} days.`}
        />
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{summary.summary}</p>
      <p className="text-xs leading-relaxed text-muted-foreground">
        Read from local work trees with the git CLI, last scanned{' '}
        {summary.last_scanned_at ? formatShortDate(summary.last_scanned_at.slice(0, 10)) : 'never'}.
        Nothing re-reads a repository on its own.
      </p>
    </div>
  )
}

/* --------------------------------------------------------------- learning */

export function LearningPanelBody({ summary }: { summary: LearningSummaryRead }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Figure
          label="Open goals"
          value={formatNumber(summary.active_goal_count)}
          hint={`${formatNumber(summary.goal_count)} recorded in total.`}
        />
        <Figure
          label="Activities in window"
          value={formatNumber(summary.activities_in_window)}
          hint={`${formatMinutes(summary.minutes_in_window)} recorded over ${formatNumber(summary.window_days)} days.`}
        />
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{summary.summary}</p>
      <p className="text-xs leading-relaxed text-muted-foreground">
        Counts of records you created. Progress on a goal stays your own figure — NEXUS never
        fills one in, and a level it cannot attribute is not shown.
      </p>
    </div>
  )
}
