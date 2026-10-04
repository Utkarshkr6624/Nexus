import { cloneElement, isValidElement, useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactElement, ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Award, Briefcase, Pencil, Plus, Sparkles } from 'lucide-react'

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
import { Spinner } from '@/components/ui/spinner'
import { formatNumber } from '@/features/analytics/format'
import {
  CAREER_EVIDENCE_TYPE_META,
  CAREER_EVIDENCE_TYPE_ORDER,
  CAREER_RECORD_KIND_META,
  CareerProfileRegion,
  CareerRecordList,
  CareerSummaryTiles,
  CareerSummaryTilesSkeleton,
  DevelopmentAreasPanel,
  PortfolioEvidenceTimeline,
  SkillOverviewGrid,
  countFromRecord,
} from '@/features/career/components'
import { useRepositories } from '@/features/developer/hooks'
import {
  useCareerEvidence,
  useCareerExperience,
  useCareerProfile,
  useCareerSummary,
  useCreateCareerEvidence,
  useCreateCareerExperience,
  useSkillGaps,
  useSkills,
  useUpsertCareerProfile,
} from '@/features/learning/hooks'
import { useProjects } from '@/features/work/hooks'
import type { ApiError } from '@/lib/api-client'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import type {
  CareerEvidenceRead,
  CareerEvidenceType,
  CareerExperienceRead,
  CareerProfileRead,
  CareerRecordKind,
  CareerSummaryRead,
  SkillRead,
  UUIDString,
} from '@/types/learning'

/**
 * The Career page.
 *
 * ## Nothing on this page was written by NEXUS
 *
 * Every field on the profile, every dated record and every manually added piece
 * of evidence is something the user typed, and nothing here expands, reworded or
 * extrapolates it. NEXUS issues no certification, infers no employer and supplies
 * no summary paragraph — the `summary` on a profile is the user's own words,
 * field for field with the column, and this page never substitutes a generated
 * one. A `repository_activity` row names code events a scan recorded; the same
 * page says "6 commits touched Python files" and never "6 Python projects
 * delivered", because there is no field that would let it write the second.
 *
 * ## A level is never a bare number
 *
 * `SkillOverviewGrid` renders every level through `describeCareerLevel` beside a
 * `LevelOriginBadge` that cannot be switched off, so a reader always sees
 * whether the number is one they set or one NEXUS estimated from recorded
 * activities. `DevelopmentAreasPanel` does the same and adds the recorded
 * activity count, in neutral language: it names the distance to a target you
 * chose and the evidence behind it, and it never says what that means about you.
 *
 * ## `available=false` is an answer, not a gap in the data
 *
 * A development area with nothing recorded against it is listed with the
 * backend's own reason rather than omitted or drawn as a zero, because "nothing
 * has been recorded for this skill yet" and "there is no distance to the target"
 * are different facts and the page has to be able to say which one it is showing.
 *
 * ## A missing profile is a state, not a failure
 *
 * `GET /career/profile` answers 404 until `PUT` has been called for the account,
 * so a 404 is routed to the region's cold-start empty state with the editor
 * beside it. Every other failure goes to the error state with a working retry.
 *
 * ## Everything view-shaped lives in the URL
 *
 * `?kind=` narrows the dated records, `?evidence=` narrows the portfolio evidence,
 * and `?experience_offset=` and `?evidence_offset=` say how far into each of those
 * lists the reader is. All four are shareable views that survive a reload and that
 * the back button steps out of rather than out of the page. The development-areas
 * evidence window is **not** a control: it is a constant stated in the panel's
 * own subtitle, because the panel's rule is only meaningful against a named
 * range and a range nobody chose by hand is a range nobody can check.
 *
 * ## Nothing on this page can be written except by the person looking at it
 *
 * The profile editor, the evidence form and the record form are the only three
 * ways to put anything on this page, and between them they hold every field the
 * backend accepts. None of them prefills, suggests or derives a title, a date, an
 * employer or a credential from anything else the account holds — the link
 * pickers name records that already exist and offer nothing beyond their names,
 * and a blank is omitted from the payload rather than sent as an empty string.
 */
const RECORD_FETCH_LIMIT = 50
const EVIDENCE_FETCH_LIMIT = 50
const SKILL_FETCH_LIMIT = 50
const LINK_LOOKUP_LIMIT = 100

/**
 * The trailing window the development-areas evidence is counted over.
 *
 * A constant rather than a picker: the panel's rule — a skill whose recorded
 * level sits below its target, with fewer related activities than the threshold
 * — is only meaningful against a stated range, so the range is stated here and
 * in the panel's own subtitle rather than left to whatever the account's default
 * happens to be configured to.
 */
const DEVELOPMENT_WINDOW_DAYS = 30

/** An absent parameter already means "every kind", so "All" needs a word. */
const ALL_KINDS = 'all'

const RECORD_KINDS: readonly CareerRecordKind[] = ['experience', 'education', 'certification']

/** One entry in a "link this to a record you already have" picker. */
type NameableOption = { id: UUIDString; name: string }

/**
 * Reads an offset out of the URL, refusing anything that is not a whole number
 * of rows.
 *
 * A hand-edited `?evidence_offset=abc` is a broken link, not a page, and must not
 * reach the request as `NaN`; `noUncheckedIndexedAccess` has nothing to say about
 * a string the network layer is about to send, so the guard lives here.
 */
function readOffset(value: string | null): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0
}

/** The offset of the last page that actually holds rows for `total`. */
function lastPageOffset(total: number, limit: number): number {
  return Math.floor((total - 1) / limit) * limit
}

/** Stable empty arrays, so the memos below are not re-created every render. */
const NO_EVIDENCE: CareerEvidenceRead[] = []
const NO_RECORDS: CareerExperienceRead[] = []
const NO_SKILLS: SkillRead[] = []

/**
 * Flattens a 422's `details.errors[]` into `{ field: message }`, per
 * `docs/api-conventions.md`. Entries that are not field-scoped are dropped and
 * the first message wins when a field repeats.
 */
function fieldErrorMessages(error: ApiError | null): Record<string, string> {
  if (!error) return {}
  const { errors } = error.fieldErrors
  if (!Array.isArray(errors)) return {}

  const messages: Record<string, string> = {}
  for (const entry of errors as Array<{ field?: unknown; message?: unknown }>) {
    const field = entry?.field
    const message = entry?.message
    if (typeof field !== 'string' || typeof message !== 'string') continue
    if (field === '' || field === 'body' || field in messages) continue
    messages[field] = message
  }
  return messages
}

export default function CareerPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [formOpen, setFormOpen] = useState(false)
  const [evidenceFormOpen, setEvidenceFormOpen] = useState(false)
  const [recordFormOpen, setRecordFormOpen] = useState(false)

  const kindParam = searchParams.get('kind')
  const kind = kindParam ?? ALL_KINDS
  const evidenceParam = searchParams.get('evidence')
  const evidenceType = evidenceParam ?? ALL_KINDS

  /**
   * Where each list is, in the URL.
   *
   * Both are offsets rather than page numbers because that is what the two
   * endpoints take, and because an offset survives a change to the page size
   * without silently meaning something else. An absent parameter is page one,
   * so the common case is a clean link.
   */
  const evidenceOffset = readOffset(searchParams.get('evidence_offset'))
  const experienceOffset = readOffset(searchParams.get('experience_offset'))

  const summary = useCareerSummary()
  const profile = useCareerProfile()
  const records = useCareerExperience({
    limit: RECORD_FETCH_LIMIT,
    offset: experienceOffset,
    kind: kind === ALL_KINDS ? undefined : (kind as CareerRecordKind),
  })
  const evidence = useCareerEvidence({
    limit: EVIDENCE_FETCH_LIMIT,
    offset: evidenceOffset,
    evidence_type: evidenceType === ALL_KINDS ? undefined : (evidenceType as CareerEvidenceType),
  })
  const skills = useSkills({ limit: SKILL_FETCH_LIMIT, offset: 0 })
  const gaps = useSkillGaps({ window_days: DEVELOPMENT_WINDOW_DAYS })

  /**
   * Names for the three things a piece of evidence can link to.
   *
   * Read from the projects, skills and repositories this page has already
   * fetched, so a row whose project is on another page of that list renders
   * without a name rather than with one this page has not read. A missing name
   * is a linkage the page cannot name, not a claim that the link does not exist.
   */
  const projects = useProjects({ limit: LINK_LOOKUP_LIMIT, offset: 0 })
  const repositories = useRepositories({ limit: LINK_LOOKUP_LIMIT, offset: 0 })

  const apply = useCallback(
    (patch: Record<string, string | undefined>) => {
      const next = new URLSearchParams(searchParams)
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined || value === ALL_KINDS) next.delete(key)
        else next.set(key, value)
      }
      setSearchParams(next)
    },
    [searchParams, setSearchParams],
  )

  /**
   * Moves one list to another offset, deleting the parameter at the start.
   *
   * A `?evidence_offset=0` says the same as no parameter at all and would make
   * two links to the same view differ, so the first page is the absent one.
   */
  const writeOffset = useCallback(
    (key: 'evidence_offset' | 'experience_offset', next: number) => {
      const params = new URLSearchParams(searchParams)
      if (next <= 0) params.delete(key)
      else params.set(key, String(next))
      setSearchParams(params)
    },
    [searchParams, setSearchParams],
  )

  /**
   * An offset past the end of the list lands on a page with no rows, and the
   * empty state would say "no evidence yet" — a claim the summary above it
   * contradicts on the same screen. So a URL that walks off the end is walked
   * back to the last page that holds something.
   *
   * Guarded on the page being *empty*: a page with rows on it is a page, whatever
   * the arithmetic says, and placeholder data is the previous answer rather than
   * an answer at all.
   */
  useEffect(() => {
    if (evidence.isPlaceholderData) return
    const page = evidence.data
    if (!page || page.total === 0 || evidenceOffset === 0 || page.items.length > 0) return
    writeOffset('evidence_offset', lastPageOffset(page.total, EVIDENCE_FETCH_LIMIT))
  }, [evidence.data, evidence.isPlaceholderData, evidenceOffset, writeOffset])

  useEffect(() => {
    if (records.isPlaceholderData) return
    const page = records.data
    if (!page || page.total === 0 || experienceOffset === 0 || page.items.length > 0) return
    writeOffset('experience_offset', lastPageOffset(page.total, RECORD_FETCH_LIMIT))
  }, [records.data, records.isPlaceholderData, experienceOffset, writeOffset])

  const skillRows = skills.data?.items ?? NO_SKILLS
  const evidenceRows = evidence.data?.items ?? NO_EVIDENCE
  const recordRows = records.data?.items ?? NO_RECORDS

  /**
   * An offset past the end of a list lands on a page with no rows, and the
   * walk-back above only corrects the URL *after* this render has already
   * happened. Rendering an empty state in that frame would claim "no evidence
   * yet" on an account holding a hundred rows, directly under a summary saying
   * otherwise — the exact "absent rows are not no rows" confusion this page is
   * written to avoid. So the frame is reported as still loading, which is true:
   * the offset is being corrected and the next render will carry rows.
   */
  const evidencePastEnd =
    !evidence.isPlaceholderData &&
    evidence.data !== undefined &&
    evidence.data.total > 0 &&
    evidenceOffset > 0 &&
    evidence.data.items.length === 0
  const recordsPastEnd =
    !records.isPlaceholderData &&
    records.data !== undefined &&
    records.data.total > 0 &&
    experienceOffset > 0 &&
    records.data.items.length === 0

  const skillNames = useMemo(() => {
    const map: Record<UUIDString, string> = {}
    for (const skill of skillRows) map[skill.id] = skill.name
    return map
  }, [skillRows])

  const projectNames = useMemo(() => {
    const map: Record<UUIDString, string> = {}
    for (const project of projects.data?.items ?? []) map[project.id] = project.name
    return map
  }, [projects.data])

  const repositoryNames = useMemo(() => {
    const map: Record<UUIDString, string> = {}
    for (const repository of repositories.data?.items ?? []) map[repository.id] = repository.name
    return map
  }, [repositories.data])

  /**
   * The link pickers, as `id`/`name` pairs.
   *
   * Only the records already fetched on this page, and only their names: a picker
   * that listed something else would be offering a qualification this page has
   * not read. A row with no link chosen stays unlinked.
   */
  const skillOptions = useMemo<NameableOption[]>(
    () => skillRows.map((skill) => ({ id: skill.id, name: skill.name })),
    [skillRows],
  )
  const projectOptions = useMemo<NameableOption[]>(
    () => (projects.data?.items ?? []).map((project) => ({ id: project.id, name: project.name })),
    [projects.data],
  )
  const repositoryOptions = useMemo<NameableOption[]>(
    () =>
      (repositories.data?.items ?? []).map((repository) => ({
        id: repository.id,
        name: repository.name,
      })),
    [repositories.data],
  )

  /**
   * A 404 on the profile read means the account has never had one.
   *
   * It is routed to the region's empty state with the editor beside it rather
   * than to an error, because "no profile yet" is the first thing a new account
   * sees and an alert with a retry button would be a worse answer than a form.
   * Ownership is the server's alone: another account's profile is a 404 too, and
   * this page never sends a user id, so there is nothing here to distinguish the
   * two and nothing it would be right to distinguish.
   */
  const profileError = profile.error ? toApiError(profile.error) : null
  const profileMissing = profileError?.isNotFound === true
  const profileErrorShown = profileError && !profileMissing ? profileError : null

  const isStale = (query: { isPlaceholderData: boolean; isFetching: boolean; isPending: boolean }) =>
    query.isPlaceholderData || (query.isFetching && !query.isPending)

  return (
    <div className="app-container space-y-6 py-6 lg:py-8">
      <PageHeader
        title="Career"
        eyebrow={
          <>
            <Briefcase className="size-3.5" aria-hidden="true" />
            Profile, records and evidence
          </>
        }
        badges={
          summary.data ? (
            <span className="text-xs text-muted-foreground">
              {summary.data.has_data ? summary.data.summary : 'Nothing recorded yet'}
            </span>
          ) : null
        }
        actions={
          <>
            <Button type="button" variant="outline" onClick={() => setRecordFormOpen(true)}>
              <Briefcase aria-hidden="true" />
              Add a record
            </Button>
            <Button type="button" variant="outline" onClick={() => setEvidenceFormOpen(true)}>
              <Award aria-hidden="true" />
              Add evidence
            </Button>
            <Button type="button" onClick={() => setFormOpen(true)}>
              <Pencil aria-hidden="true" />
              {profile.data ? 'Edit profile' : 'Create profile'}
            </Button>
          </>
        }
        description="Everything on this page is yours. The profile, the dated records and the evidence are what you typed, and NEXUS writes none of them for you: it issues no certification, infers no employer and generates no summary. A skill level is always shown with where it came from — one you set, or one NEXUS estimated from recorded activity — because a number nobody can attribute is not a level."
      />

      <section aria-labelledby="career-overview" className="space-y-3">
        <h2 id="career-overview" className="sr-only">
          Overview
        </h2>
        {summary.isPending && !summary.data ? (
          <CareerSummaryTilesSkeleton />
        ) : (
          <CareerSummaryTiles
            summary={summary.data ?? null}
            isStale={isStale(summary)}
            error={summary.isError && !summary.isPlaceholderData ? toApiError(summary.error) : null}
            onRetry={() => void summary.refetch()}
            titleLevel="h3"
          />
        )}
      </section>

      <section aria-labelledby="career-profile" className="space-y-3">
        <div className="min-w-0 space-y-1">
          <h2 id="career-profile" className="text-base font-semibold text-foreground">
            Profile
          </h2>
          <p className="text-xs text-muted-foreground">
            Your own words, field for field. NEXUS never rewrites the summary or picks a target role
            for you — if a field is empty, it is empty because you left it empty.
          </p>
        </div>

        <CareerProfileRegion
          profile={profileMissing ? null : (profile.data ?? null)}
          isLoading={profile.isPending}
          isStale={isStale(profile)}
          error={profileErrorShown}
          onRetry={() => void profile.refetch()}
          emptyReason={
            summary.data && summary.data.has_profile
              ? 'A profile exists on this account, but the read above did not return it. Retrying will fetch it again.'
              : null
          }
          emptyAction={
            <Button type="button" onClick={() => setFormOpen(true)}>
              <Pencil aria-hidden="true" />
              Write your profile
            </Button>
          }
          titleLevel="h3"
        />
      </section>

      <section aria-labelledby="career-skills" className="space-y-3">
        <div className="min-w-0 space-y-1">
          <h2 id="career-skills" className="text-base font-semibold text-foreground">
            Skill overview
          </h2>
          <p className="text-xs text-muted-foreground">
            Each tile shows where a skill is against the target you set for it, beside the source of
            that level and the number of recorded activities behind it.
          </p>
        </div>

        <SkillOverviewGrid
          skills={skillRows}
          isLoading={skills.isPending && !skills.data}
          isStale={skills.isPlaceholderData}
          error={skills.isError && !skills.isPlaceholderData ? toApiError(skills.error) : null}
          onRetry={() => void skills.refetch()}
          emptyReason="Skills are tracked on the Learning page. Add one there and it appears here with its level, the source of that level and the activities recorded against it."
          skeletonCount={6}
          titleLevel="h3"
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <DevelopmentAreasPanel
          gaps={gaps.data ?? []}
          windowDays={DEVELOPMENT_WINDOW_DAYS}
          isLoading={gaps.isPending && !gaps.data}
          isStale={isStale(gaps)}
          error={gaps.isError && !gaps.isPlaceholderData ? toApiError(gaps.error) : null}
          onRetry={() => void gaps.refetch()}
          emptyReason="A development area is a skill whose recorded level sits below the target you set for it, with fewer related activities in the window than the panel's threshold. None of your skills currently meets that description."
          titleLevel="h3"
          subtitle="A distance between a level and a target you chose, with the recorded activity behind it. It is not a ranking, a verdict or a gap in what you can do — it is arithmetic on two numbers you set."
        />

        <EvidenceCounts summary={summary.data ?? null} />
      </div>

      <section aria-labelledby="career-evidence" className="space-y-3">
        <div className="min-w-0 space-y-1">
          <h2 id="career-evidence" className="text-base font-semibold text-foreground">
            Portfolio evidence
          </h2>
          <p className="text-xs text-muted-foreground">
            Rows you added, and rows a subsystem recorded about a record you created — each labelled
            with which of the two it was. Repository evidence names code events: it is not a count of
            projects delivered.
          </p>
        </div>

        <div className="rounded-lg border border-border bg-card p-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="app-form-field">
              <Label htmlFor="career-evidence-type">Kind of evidence</Label>
              <Select
                id="career-evidence-type"
                value={evidenceType}
                onChange={(event) =>
                  apply({ evidence: event.target.value, evidence_offset: undefined })
                }
              >
                <option value={ALL_KINDS}>All kinds</option>
                {CAREER_EVIDENCE_TYPE_ORDER.map((type) => (
                  <option key={type} value={type}>
                    {CAREER_EVIDENCE_TYPE_META[type].label}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <LiveStatus active={evidence.isPlaceholderData} className="mt-3 text-xs text-muted-foreground">
            Updating for the selected filter…
          </LiveStatus>
        </div>

        <PortfolioEvidenceTimeline
          evidence={evidenceRows}
          byType={evidence.data?.by_type ?? null}
          isLoading={(evidence.isPending && !evidence.data) || evidencePastEnd}
          isStale={isStale(evidence)}
          error={
            evidence.isError && !evidence.isPlaceholderData ? toApiError(evidence.error) : null
          }
          onRetry={() => void evidence.refetch()}
          projectName={(id) => projectNames[id] ?? null}
          skillName={(id) => skillNames[id] ?? null}
          repositoryName={(id) => repositoryNames[id] ?? null}
          total={evidence.data?.total ?? null}
          titleLevel="h3"
          emptyReason={
            evidenceType === ALL_KINDS
              ? null
              : 'Evidence exists on this profile; none of it is of the kind selected above. Showing every kind brings the rest back.'
          }
          subtitle="Grouped by kind, newest first within each group. Every kind is listed separately — a certification you supplied and a milestone recorded against a goal are not the same evidence, and neither is merged into the other."
        />

        <Pager
          label="Portfolio evidence pages"
          subject="evidence rows"
          total={evidence.data?.total ?? null}
          offset={evidenceOffset}
          limit={EVIDENCE_FETCH_LIMIT}
          onOffset={(next) => writeOffset('evidence_offset', next)}
        />
      </section>

      <section aria-labelledby="career-records" className="space-y-3">
        <div className="min-w-0 space-y-1">
          <h2 id="career-records" className="text-base font-semibold text-foreground">
            Education, experience and certifications
          </h2>
          <p className="text-xs text-muted-foreground">
            Dated records, in the order they arrived. A role you held and a project you completed are
            different things and are never rendered with the same weight.
          </p>
        </div>

        <div className="rounded-lg border border-border bg-card p-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="app-form-field">
              <Label htmlFor="career-record-kind">Record kind</Label>
              <Select
                id="career-record-kind"
                value={kind}
                onChange={(event) => apply({ kind: event.target.value, experience_offset: undefined })}
              >
                <option value={ALL_KINDS}>All kinds</option>
                {RECORD_KINDS.map((option) => (
                  <option key={option} value={option}>
                    {CAREER_RECORD_KIND_META[option].label}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <LiveStatus active={records.isPlaceholderData} className="mt-3 text-xs text-muted-foreground">
            Updating for the selected filter…
          </LiveStatus>
        </div>

        <CareerRecordList
          records={recordRows}
          isLoading={(records.isPending && !records.data) || recordsPastEnd}
          isStale={isStale(records)}
          error={records.isError && !records.isPlaceholderData ? toApiError(records.error) : null}
          onRetry={() => void records.refetch()}
          title="Dated records"
          titleLevel="h3"
          emptyReason={
            kind === ALL_KINDS
              ? 'These records are yours to write. Nothing here is generated, and NEXUS will not draft a role or a qualification for you.'
              : 'Records of other kinds exist on this profile; none of them is of the kind selected above. Showing every kind brings the rest back.'
          }
          skeletonCount={4}
        />

        {/* `total` is deliberately not handed to the list: it renders a
            "this is a position in the list" note, which is now what the pager
            two lines below says — with the page number, which that note cannot
            give. The timeline keeps its `total` because it always renders a
            scope note, and "everything on file is shown here" would be a lie on
            any page but the first. */}
        <Pager
          label="Dated record pages"
          subject="records"
          total={records.data?.total ?? null}
          offset={experienceOffset}
          limit={RECORD_FETCH_LIMIT}
          onOffset={(next) => writeOffset('experience_offset', next)}
        />
      </section>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Reading this page: an evidence row is a fact about a record — a project reached a status, a
        scan read a repository, you typed an entry. None of them measures what the work achieved, and
        none of them is combined here into a readiness score, an employer match or a suitability
        claim. The numbers on it are counts of the rows and records you own.
      </p>

      <EditProfileDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        profile={profileMissing ? null : (profile.data ?? null)}
      />

      <AddEvidenceDialog
        open={evidenceFormOpen}
        onOpenChange={setEvidenceFormOpen}
        projects={projectOptions}
        skills={skillOptions}
        repositories={repositoryOptions}
      />

      <AddRecordDialog open={recordFormOpen} onOpenChange={setRecordFormOpen} />
    </div>
  )
}

/**
 * Previous and next over one server-paginated list.
 *
 * **The offset lives in the URL**, so "the second page of evidence" is a link
 * somebody can be sent, and the back button steps back through pages rather than
 * out of the page. Both lists carry their own offset, so turning the pages of one
 * leaves the other where the reader left it.
 *
 * **Rendered only when there is more than one page.** A pager over a single page
 * is two disabled buttons and a count the list already states; it says nothing a
 * reader did not have. `total` is the backend's figure across every matching
 * row, so a filter that narrows the set moves both boundaries rather than
 * stranding the reader on a page that no longer exists.
 */
function Pager({
  label,
  subject,
  total,
  offset,
  limit,
  onOffset,
}: {
  label: string
  subject: string
  total: number | null
  offset: number
  limit: number
  onOffset: (next: number) => void
}) {
  if (total === null || total <= limit) return null

  const page = Math.floor(offset / limit) + 1
  const pages = Math.ceil(total / limit)
  // An offset past the end is corrected by the effect above; until it lands, the
  // caption names the last real page rather than a page that does not exist.
  const shown = Math.min(page, pages)

  return (
    <nav className="flex flex-wrap items-center justify-between gap-3" aria-label={label}>
      <p className="text-xs text-muted-foreground">
        Page {shown} of {pages} · {formatNumber(total)} matching {subject}
      </p>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onOffset(offset - limit)}
        >
          Previous
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page >= pages}
          onClick={() => onOffset(offset + limit)}
        >
          Next
        </Button>
      </div>
    </nav>
  )
}

/**
 * Two evidence figures, read from the summary's own counts.
 *
 * `countFromRecord` returns null for a key the response does not carry, and
 * null renders as a dash with the reason — not as a zero. A sparse
 * `by_type` record is the backend declining to state a count, and reading the
 * missing key as `0` would claim it counted something it never sent.
 */
function EvidenceCounts({ summary }: { summary: CareerSummaryRead | null }) {
  const rows: { label: string; key: string; hint: string }[] = [
    {
      label: 'Linked to a skill',
      key: 'skill_activity',
      hint: 'Evidence rows that point at one of your tracked skills. The skill carries the level and where it came from.',
    },
    {
      label: 'Linked to a repository',
      key: 'repository_activity',
      hint: 'Evidence rows a repository scan recorded — commits, branches, changed lines. Code events, not delivered projects.',
    },
    {
      label: 'Manually added',
      key: 'achievement',
      hint: 'Achievements you wrote into your profile. Stored verbatim and never expanded.',
    },
  ]

  return (
    <Card className="min-w-0">
      <CardHeader className="pb-4">
        <CardTitle level="h3">
          <span className="inline-flex items-center gap-2">
            <Sparkles className="size-4" aria-hidden="true" />
            How this evidence is made up
          </span>
        </CardTitle>
        <CardDescription>
          Counts across every evidence row on this account, read from the career summary. A kind the
          response does not carry shows a dash rather than a zero, because a missing key is the
          server declining to state a count.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="space-y-3">
          {rows.map((row) => {
            const value = countFromRecord(summary?.by_type, row.key)
            return (
              <div key={row.key} className="min-w-0 space-y-0.5">
                <div className="flex items-baseline justify-between gap-3">
                  <dt className="text-sm text-muted-foreground">{row.label}</dt>
                  <dd className="text-sm font-medium tabular-nums text-foreground">
                    {value === null ? '—' : value.toLocaleString()}
                  </dd>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">{row.hint}</p>
              </div>
            )
          })}
        </dl>
      </CardContent>
    </Card>
  )
}

/* ---------------------------------------------------------------------- form */

/**
 * Writes the career profile.
 *
 * **A `PUT` upsert, so the same body twice leaves the same profile** rather than
 * a conflict or a second row: the backend keys it on the account's unique
 * `user_id`. Nothing in the payload is generated — there is no field for a
 * summary NEXUS wrote, a target role it inferred from your skills, or a
 * highlight it chose — and the form prefills from the profile it just fetched,
 * but what it sends is still what the person typed.
 *
 * `links` replaces the whole list rather than appending, which is what a `PUT`
 * means. Each line is one URL and blank lines are dropped, so an empty box
 * clears the list rather than storing one empty string.
 *
 * **A 422 is rendered under the field it names**, using the server's own
 * message, because a single banner at the top leaves the reader guessing which
 * of six inputs was refused.
 */
function EditProfileDialog({
  open,
  onOpenChange,
  profile,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  profile: CareerProfileRead | null
}) {
  const [error, setError] = useState<ApiError | null>(null)

  const upsert = useUpsertCareerProfile()
  const pending = upsert.isPending

  const fieldErrors = fieldErrorMessages(error)
  const bannerError =
    error && !(error.isValidationError && Object.keys(fieldErrors).length > 0) ? error : null

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    // Uncontrolled inputs, read once on submit: the form is re-keyed on the
    // profile's own `updated_at`, so reopening it always seeds from the stored
    // row without a reset function that could disagree with what is on screen.
    const form = new FormData(event.currentTarget)
    const text = (name: string): string | null => {
      const value = String(form.get(name) ?? '').trim()
      return value.length > 0 ? value : null
    }
    const urls = String(form.get('links') ?? '')
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0)

    try {
      await upsert.mutateAsync({
        target_role: text('target_role'),
        target_domain: text('target_domain'),
        headline: text('headline'),
        summary: text('summary'),
        location: text('location'),
        links: urls,
      })
      toast.success('Profile saved', 'Every field on it is exactly what you typed.')
      onOpenChange(false)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error('Could not save that profile', apiError.message)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (pending) return
        onOpenChange(next)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{profile ? 'Edit your career profile' : 'Write your career profile'}</DialogTitle>
          <DialogDescription>
            Every field here is yours. NEXUS stores what you write and does not expand it, so a
            certification, an employer or a date only ever appears because you supplied it. Saving
            replaces the profile — it is an upsert, not an append.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <form
            className="app-form-stack"
            onSubmit={submit}
            noValidate
            key={profile?.updated_at ?? 'new-profile'}
          >
            {bannerError && (
              <p role="alert" className="app-form-error">
                {bannerError.message}
              </p>
            )}

            <Field id="profile-role" label="Target role" optional error={fieldErrors.target_role}>
              <Input
                id="profile-role"
                name="target_role"
                defaultValue={profile?.target_role ?? ''}
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.target_role) || undefined}
              />
            </Field>

            <Field
              id="profile-domain"
              label="Target domain"
              optional
              error={fieldErrors.target_domain}
            >
              <Input
                id="profile-domain"
                name="target_domain"
                defaultValue={profile?.target_domain ?? ''}
                maxLength={120}
                aria-invalid={Boolean(fieldErrors.target_domain) || undefined}
              />
            </Field>

            <Field
              id="profile-headline"
              label="Headline"
              optional
              error={fieldErrors.headline}
              hint="One line about what you do, in your words."
            >
              <Input
                id="profile-headline"
                name="headline"
                defaultValue={profile?.headline ?? ''}
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.headline) || undefined}
              />
            </Field>

            <Field
              id="profile-summary"
              label="Summary"
              optional
              error={fieldErrors.summary}
              hint="Your own paragraph. NEXUS never writes one for you and never rewrites this."
            >
              <textarea
                id="profile-summary"
                name="summary"
                rows={5}
                defaultValue={profile?.summary ?? ''}
                aria-invalid={Boolean(fieldErrors.summary) || undefined}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background aria-[invalid=true]:border-destructive"
              />
            </Field>

            <Field id="profile-location" label="Location" optional error={fieldErrors.location}>
              <Input
                id="profile-location"
                name="location"
                defaultValue={profile?.location ?? ''}
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.location) || undefined}
              />
            </Field>

            <Field
              id="profile-links"
              label="Portfolio links"
              optional
              error={fieldErrors.links}
              hint="One address per line. Leave empty for none — an empty list is a fact about your profile, not a missing field."
            >
              <textarea
                id="profile-links"
                name="links"
                rows={3}
                spellCheck={false}
                defaultValue={(profile?.links ?? []).join('\n')}
                aria-invalid={Boolean(fieldErrors.links) || undefined}
                className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background aria-[invalid=true]:border-destructive"
              />
            </Field>

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                disabled={pending}
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? (
                  <>
                    <Spinner size="sm" />
                    Saving…
                  </>
                ) : (
                  'Save profile'
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
 * Adds one piece of career evidence.
 *
 * **Nothing on this form is filled in from anything else on the page.** The
 * three link pickers offer records that already exist, because a link names a
 * record rather than inventing one, and everything else starts empty: there is
 * no suggestion, no prefill from a project, no date defaulted to today and no
 * title assembled from a project name. An achievement NEXUS wrote would be an
 * invented qualification, and the whole form exists so that it never can be.
 *
 * **The date is required, and the message is on the field.** The write schema
 * leaves `occurred_on` optional and the service refuses an undated row, so the
 * refusal arrives as a validation error with no field to hang it on. Checking it
 * here puts the reason under the control that caused it instead of in a banner
 * at the top of a dialog — which is also why the submit button is not disabled on
 * a blank date, since a disabled button can never be pressed to be told.
 *
 * **No blank is sent as an empty string.** Every optional field is omitted
 * rather than sent as `""`, which is what a `PUT` reads as a cleared value and
 * what a `POST` would store as a value the user never typed.
 *
 * `source` is never sent: it defaults to `manual` server-side, which is the only
 * honest provenance for anything typed into this form.
 */
function AddEvidenceDialog({
  open,
  onOpenChange,
  projects,
  skills,
  repositories,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  projects: readonly NameableOption[]
  skills: readonly NameableOption[]
  repositories: readonly NameableOption[]
}) {
  const [evidenceType, setEvidenceType] = useState<CareerEvidenceType>('achievement')
  const [title, setTitle] = useState('')
  const [occurredOn, setOccurredOn] = useState('')
  const [description, setDescription] = useState('')
  const [projectId, setProjectId] = useState('')
  const [skillId, setSkillId] = useState('')
  const [repositoryId, setRepositoryId] = useState('')
  const [error, setError] = useState<ApiError | null>(null)
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({})

  const create = useCreateCareerEvidence()
  const pending = create.isPending

  const fieldErrors = { ...localErrors, ...fieldErrorMessages(error) }
  const bannerError =
    error && !(error.isValidationError && Object.keys(fieldErrorMessages(error)).length > 0)
      ? error
      : null

  function reset() {
    setEvidenceType('achievement')
    setTitle('')
    setOccurredOn('')
    setDescription('')
    setProjectId('')
    setSkillId('')
    setRepositoryId('')
    setError(null)
    setLocalErrors({})
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setLocalErrors({})

    const name = title.trim()
    const problems: Record<string, string> = {}
    if (!name) {
      problems.title = 'Evidence needs the line you wrote for it. NEXUS will not write one.'
    }
    if (!occurredOn) {
      problems.occurred_on =
        'Evidence must carry the date it happened on. A row with no date cannot be placed in order, so the server will not store one.'
    }
    if (Object.keys(problems).length > 0) {
      setLocalErrors(problems)
      return
    }

    try {
      const saved = await create.mutateAsync({
        evidence_type: evidenceType,
        title: name,
        occurred_on: occurredOn,
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(projectId ? { project_id: projectId } : {}),
        ...(skillId ? { skill_id: skillId } : {}),
        ...(repositoryId ? { repository_id: repositoryId } : {}),
      })
      toast.success('Evidence added', `"${saved.title}" is on your profile as you wrote it.`)
      reset()
      onOpenChange(false)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error('Could not add that evidence', apiError.message)
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
          <DialogTitle>Add a piece of evidence</DialogTitle>
          <DialogDescription>
            One thing you can point at. The title, the date and the note are yours to write and are
            stored exactly as written — NEXUS supplies none of them, issues no credential and looks
            none up.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <form className="app-form-stack" onSubmit={submit} noValidate>
            {bannerError && (
              <p role="alert" className="app-form-error">
                {bannerError.message}
              </p>
            )}

            <Field
              id="evidence-type"
              label="Kind of evidence"
              error={fieldErrors.evidence_type}
              hint={CAREER_EVIDENCE_TYPE_META[evidenceType].description}
            >
              <Select
                id="evidence-type"
                value={evidenceType}
                aria-invalid={Boolean(fieldErrors.evidence_type) || undefined}
                onChange={(event) => {
                  setEvidenceType(event.target.value as CareerEvidenceType)
                  setError(null)
                }}
              >
                {CAREER_EVIDENCE_TYPE_ORDER.map((type) => (
                  <option key={type} value={type}>
                    {CAREER_EVIDENCE_TYPE_META[type].label}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="evidence-title"
              label="What it was"
              error={fieldErrors.title}
              hint="One line, in your words."
            >
              <Input
                id="evidence-title"
                value={title}
                required
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.title) || undefined}
                onChange={(event) => {
                  setTitle(event.target.value)
                  setError(null)
                  setLocalErrors({})
                }}
              />
            </Field>

            <Field
              id="evidence-date"
              label="Date it happened"
              error={fieldErrors.occurred_on}
              hint="Required. Evidence is read in date order, and a row with no date cannot be ordered."
            >
              <Input
                id="evidence-date"
                type="date"
                value={occurredOn}
                aria-invalid={Boolean(fieldErrors.occurred_on) || undefined}
                onChange={(event) => {
                  setOccurredOn(event.target.value)
                  setError(null)
                  setLocalErrors({})
                }}
              />
            </Field>

            <Field
              id="evidence-description"
              label="Description"
              optional
              error={fieldErrors.description}
              hint="A longer note, kept as typed. Leave empty and none is recorded."
            >
              <textarea
                id="evidence-description"
                rows={4}
                maxLength={20000}
                value={description}
                aria-invalid={Boolean(fieldErrors.description) || undefined}
                onChange={(event) => setDescription(event.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background aria-[invalid=true]:border-destructive"
              />
            </Field>

            <Field
              id="evidence-project"
              label="Linked project"
              optional
              error={fieldErrors.project_id}
              hint="Points the row at a project you created. It names the project; it says nothing about what the project achieved."
            >
              <Select
                id="evidence-project"
                value={projectId}
                aria-invalid={Boolean(fieldErrors.project_id) || undefined}
                onChange={(event) => setProjectId(event.target.value)}
              >
                <option value="">Not linked to a project</option>
                {projects.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="evidence-skill"
              label="Linked skill"
              optional
              error={fieldErrors.skill_id}
              hint="Points the row at a skill you track. The skill carries its own level and where that level came from."
            >
              <Select
                id="evidence-skill"
                value={skillId}
                aria-invalid={Boolean(fieldErrors.skill_id) || undefined}
                onChange={(event) => setSkillId(event.target.value)}
              >
                <option value="">Not linked to a skill</option>
                {skills.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="evidence-repository"
              label="Linked repository"
              optional
              error={fieldErrors.repository_id}
              hint="Points the row at a repository scan already knows about. Leave it empty unless that is where this happened."
            >
              <Select
                id="evidence-repository"
                value={repositoryId}
                aria-invalid={Boolean(fieldErrors.repository_id) || undefined}
                onChange={(event) => setRepositoryId(event.target.value)}
              >
                <option value="">Not linked to a repository</option>
                {repositories.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name}
                  </option>
                ))}
              </Select>
            </Field>

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                disabled={pending}
                onClick={() => onOpenChange(false)}
              >
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
                    <Plus aria-hidden="true" />
                    Save evidence
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
 * Adds one dated record: a role, a course or a certification.
 *
 * **An absent end date means the record is current**, which is a fact about the
 * record rather than a field left unfilled, so the box is optional and a blank
 * one is *omitted* from the payload — sending `""` would ask the backend to
 * store an empty string as an end date, and `null` would ask it to store a date
 * of nothing.
 *
 * **A start date is not defaulted to today** and an end date is not filled in
 * either. Those are the user's dates, and a form that supplied one would be
 * writing a career history for them.
 *
 * The end-before-start refusal is checked here as well as server-side, because a
 * validation error with no field attached lands in a banner and leaves the reader
 * guessing which of the two date boxes is wrong.
 */
function AddRecordDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [kind, setKind] = useState<CareerRecordKind>('experience')
  const [title, setTitle] = useState('')
  const [organisation, setOrganisation] = useState('')
  const [startedOn, setStartedOn] = useState('')
  const [endedOn, setEndedOn] = useState('')
  const [description, setDescription] = useState('')
  const [url, setUrl] = useState('')
  const [error, setError] = useState<ApiError | null>(null)
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({})

  const create = useCreateCareerExperience()
  const pending = create.isPending

  const fieldErrors = { ...localErrors, ...fieldErrorMessages(error) }
  const bannerError =
    error && !(error.isValidationError && Object.keys(fieldErrorMessages(error)).length > 0)
      ? error
      : null

  function reset() {
    setKind('experience')
    setTitle('')
    setOrganisation('')
    setStartedOn('')
    setEndedOn('')
    setDescription('')
    setUrl('')
    setError(null)
    setLocalErrors({})
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setLocalErrors({})

    const name = title.trim()
    const problems: Record<string, string> = {}
    if (!name) {
      problems.title = 'A record needs the name you gave it.'
    }
    if (startedOn && endedOn && endedOn < startedOn) {
      problems.ended_on =
        'An end date before the start date is refused. Leave the end date empty if the record is still current.'
    }
    if (Object.keys(problems).length > 0) {
      setLocalErrors(problems)
      return
    }

    try {
      const saved = await create.mutateAsync({
        kind,
        title: name,
        ...(organisation.trim() ? { organisation: organisation.trim() } : {}),
        ...(startedOn ? { started_on: startedOn } : {}),
        ...(endedOn ? { ended_on: endedOn } : {}),
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(url.trim() ? { url: url.trim() } : {}),
      })
      toast.success('Record added', `"${saved.title}" is on your profile with the dates you gave.`)
      reset()
      onOpenChange(false)
    } catch (cause) {
      const apiError = toApiError(cause)
      setError(apiError)
      toast.error('Could not add that record', apiError.message)
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
          <DialogTitle>Add a dated record</DialogTitle>
          <DialogDescription>
            A role, a course or a certification — one line on the profile, with the dates you give
            it. NEXUS looks no qualification up, fills in no employer and invents no end date.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <form className="app-form-stack" onSubmit={submit} noValidate>
            {bannerError && (
              <p role="alert" className="app-form-error">
                {bannerError.message}
              </p>
            )}

            <Field
              id="record-kind"
              label="Kind of record"
              error={fieldErrors.kind}
              hint={CAREER_RECORD_KIND_META[kind].description}
            >
              <Select
                id="record-kind"
                value={kind}
                aria-invalid={Boolean(fieldErrors.kind) || undefined}
                onChange={(event) => {
                  setKind(event.target.value as CareerRecordKind)
                  setError(null)
                }}
              >
                {RECORD_KINDS.map((option) => (
                  <option key={option} value={option}>
                    {CAREER_RECORD_KIND_META[option].label}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              id="record-title"
              label="Title"
              error={fieldErrors.title}
              hint="Your own name for it — the role, the course, or the certification as it is written on it."
            >
              <Input
                id="record-title"
                value={title}
                required
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.title) || undefined}
                onChange={(event) => {
                  setTitle(event.target.value)
                  setError(null)
                  setLocalErrors({})
                }}
              />
            </Field>

            <Field
              id="record-organisation"
              label="Organisation"
              optional
              error={fieldErrors.organisation}
              hint="Who issued it, or where it was done. Leave empty when there is no such body — that is an answer, not a gap."
            >
              <Input
                id="record-organisation"
                value={organisation}
                maxLength={200}
                aria-invalid={Boolean(fieldErrors.organisation) || undefined}
                onChange={(event) => setOrganisation(event.target.value)}
              />
            </Field>

            <Field
              id="record-start"
              label="Start date"
              optional
              error={fieldErrors.started_on}
              hint="When it began, as you gave it. NEXUS does not fill one in."
            >
              <Input
                id="record-start"
                type="date"
                value={startedOn}
                aria-invalid={Boolean(fieldErrors.started_on) || undefined}
                onChange={(event) => {
                  setStartedOn(event.target.value)
                  setError(null)
                  setLocalErrors({})
                }}
              />
            </Field>

            <Field
              id="record-end"
              label="End date"
              optional
              error={fieldErrors.ended_on}
              hint="Leave empty for something current. An absent end date is how a record says it has not ended."
            >
              <Input
                id="record-end"
                type="date"
                min={startedOn || undefined}
                value={endedOn}
                aria-invalid={Boolean(fieldErrors.ended_on) || undefined}
                onChange={(event) => {
                  setEndedOn(event.target.value)
                  setError(null)
                  setLocalErrors({})
                }}
              />
            </Field>

            <Field
              id="record-description"
              label="Description"
              optional
              error={fieldErrors.description}
              hint="What it involved, in your words. Stored as typed."
            >
              <textarea
                id="record-description"
                rows={4}
                maxLength={20000}
                value={description}
                aria-invalid={Boolean(fieldErrors.description) || undefined}
                onChange={(event) => setDescription(event.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background aria-[invalid=true]:border-destructive"
              />
            </Field>

            <Field
              id="record-url"
              label="Link"
              optional
              error={fieldErrors.url}
              hint="An address you are supplying. Leave empty and none is recorded."
            >
              <Input
                id="record-url"
                type="url"
                inputMode="url"
                spellCheck={false}
                value={url}
                maxLength={500}
                aria-invalid={Boolean(fieldErrors.url) || undefined}
                onChange={(event) => setUrl(event.target.value)}
                className="font-mono"
              />
            </Field>

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                disabled={pending}
                onClick={() => onOpenChange(false)}
              >
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
                    <Plus aria-hidden="true" />
                    Save record
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