"""Phase 7 detection: one pass over the recorded data, one reconciled risk set.

The shape of a pass
-------------------
:meth:`RiskDetectionService.evaluate` is one read, seven detectors, and one
reconciliation. The detectors themselves live in :mod:`app.services.risk.scoring`
as pure functions; everything this module adds is the part that needs a database
— the reads, the bookkeeping, and the decision about what to persist.

Two rules do most of the work, and both of them are rules about **not** writing a
row.

**An unavailable detector writes nothing.** Every detector can answer "I cannot
judge this", and a detector that says so has said something true and useful: a
user in their first fortnight has no earlier period to compare against, so the
consistency detector is in no position to say their activity fell. Persisting
nothing and saying why is the correct output. Persisting a fabricated 0 is not —
it is the "confident 0 that reads as *you are fine*" failure the whole analytics
engine is built to avoid, and inside a training matrix it is worse, because a
fabricated zero is indistinguishable from an observed one once it is in there.
The reason travels on the run summary so the UI can say "not enough data" rather
than showing an empty Risk Center and letting the user assume it means "nothing
is wrong".

**A detector that measures zero writes nothing either.** This one is a judgement
call and it is worth stating plainly: a score of exactly 0 is a *measurement*
that a condition is not present, and a measurement belongs in the run summary,
not in a table of things to act on. A row reading "No scheduling conflicts
detected" in a list whose job is to show what needs attention is noise the user
has to dismiss by hand, every run, forever. The measurement is still recorded —
on the evaluation row, which is where a trend over time belongs — and the risk row
it would have produced is not. One consequence is worth naming: a zero is
deliberately *not* added to the identities the resolution sweep treats as
re-detected, so a condition that has genuinely gone away closes its own row.

Without both rules the Risk Center is a list of everything, which is the same as
a list of nothing. With them it is a list of what is currently true, and the
sweep in :meth:`RiskDetectionService._resolve_stale` is what keeps it honest.

**A cap is a limit on what is written, never a claim that the rest is fine.**

A pass writes at most :data:`MAX_DEADLINE_TASKS` deadline risks,
:data:`MAX_PROJECT_RISKS` project risks and :data:`MAX_TASK_RISKS` task risks,
and it reads a bounded page of candidates for each. Both bounds used to be
silent, and the write caps were the worse of the two: a project or a task that
was **still at risk** but fell beyond the cap simply was not in this pass's
output, so the sweep read its absence as "the condition went away" and closed a
live risk — recording the false reason "the condition behind it was not detected
in this evaluation" on a condition NEXUS had merely declined to look at. Five of
thirty blocked tasks disappeared on one pass and could not come back, because the
sweep is terminal.

So a cap now has two halves. What the cap drops is **deferred**, not resolved:
those identities are carried to the sweep as ones to leave alone, and the count
goes onto the run summary next to every other coverage note. And where the bound
is on the *read* rather than the write — a candidate scan that did not reach the
end of the set — the affected risk types are excluded from the sweep altogether,
because "I did not look" and "it is gone" must never produce the same row.

Both halves are the same rule: **a risk is closed only by a pass that was in a
position to judge every candidate of its type.**

Nothing is derived twice
------------------------
Every figure the detectors consume is read from
:class:`~app.services.analytics.service.AnalyticsService`, which computed it in
Phase 6 and already carries an ``available`` flag and a reason for the ones it
could not compute. The service is **injected**, not reconstructed from
repositories here, and that is structural rather than stylistic: a service holding
the same repositories could re-derive anything it liked and the duplication would
be invisible. Holding the analytics service means the only way to get a figure is
to ask the thing that owns it.

Two inputs are read from the repositories directly, and both are inputs Phase 6
genuinely does not publish on a read: the raw ``(estimated, actual)`` pairs
behind :class:`~app.schemas.analytics.EstimationAccuracyRead`, because the
estimation detector needs the *distribution* of the error and the read carries
only its mean; and the per-row task and project detail, because every Phase 6
project and task roll-up is an aggregate and an aggregate cannot answer a
question about one row.

A third read is assembled here rather than taken from a collaborator: the
per-task reschedule counts behind the task-level detector. A reschedule is a
due-date edit and has no column of its own anywhere in the schema, so the event
feed is the only place the fact exists — and the count has to be grouped *by
task*, which is the one shape
:meth:`~app.repositories.analytics.AnalyticsRepository.task_event_count` does not
return. Asking for it once per candidate task instead would put an N+1 in the
middle of a pass that is otherwise a fixed number of round trips.

Why a seventh detector
----------------------
Six detectors describe a window or a plan: a deadline, a load, a habit, a project,
a calendar. Blocked work and repeated rescheduling are properties of **one task**,
and the brief names both — "IF task repeatedly rescheduled THEN recommend breaking
task into subtasks" is its own worked example. Neither can be expressed by the
project detector, which can only *count* a project's blocked tasks: that is a fact
about the project and says nothing about the task a suggestion would have to name.
:class:`~app.models.enums.RiskType` has carried a ``task`` member from the start and
:data:`app.services.risk.recommendation.recommendation_rules` maps two rules onto
it, so before this detector existed those two rules were unreachable and the brief's
example had no path to the screen. They now share one identity — a risk of type
``task`` pointing at the task row — which is also what lets the two rules agree
with the detector about what "repeatedly rescheduled" means.

One gap, and how the summary carries the answer
-----------------------------------------------
``risk_evaluations`` has no column for "detectors that declined to judge", and the
``EvaluationRead`` wire shape has no list for them either. The run summary
therefore carries those reasons — and the reasons for the detectors that measured
zero — in ``reason_if_not_evaluated``. ``evaluated`` is true when at least one
detector could judge something, which is the question that field's name implies
and the question an empty Risk Center needs answered.

For that flag to be worth anything, *every* detector has to be able to decline.
Six of the seven always could; the scheduling detector could not, because its
four inputs are counts and a count of zero reads as a measurement. That reading
is right when the plan is real and wrong when there is no plan at all — an
account with no scheduled sessions has nothing the four counts could have found,
and reporting "no conflicts detected" is the confident zero this module refuses
to store anywhere else. So the count of sessions the detector actually walked is
now one of its inputs, and an empty plan makes it decline like the rest. That
makes ``evaluated: false`` reachable, which is the only way the distinction the
Risk Center needs — "no significant risk detected yet" against "not enough data
to assess this yet" — can be drawn at all: both present as an empty list, and
only one of them is an answer the engine stands behind.

Language
--------
Every title and description in this module is **neutral and factual**: it
describes what the recorded rows say, and never what that implies about the
person who recorded them. The brief is explicit about this, and the same rule
bans inferring fatigue from a run of sessions, motivation from a quiet week and
health from a backlog. All of the wording is built by the seven ``_wording``
functions grouped at the end of the module, so the complete vocabulary of things
a user can be told is reviewable in one screen.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger, log_event
from app.models.activity import ActivityLog
from app.models.enums import ActivityEvent, RiskSeverity, RiskStatus, RiskType, TaskStatus
from app.models.task import Task
from app.repositories.analytics import AnalyticsRepository
from app.repositories.project import ProjectRepository
from app.repositories.task import TaskRepository
from app.schemas.analytics import (
    ConsistencyRead,
    DeadlineAdherenceRead,
    EstimationAccuracyRead,
    ProjectAnalyticsRead,
    WorkloadRead,
)
from app.schemas.risk import EvaluationRead
from app.services.activity_service import ActivityService
from app.services.analytics.service import AnalyticsService
from app.services.risk.scoring import (
    NOT_ENOUGH_DATA,
    TASK_RESCHEDULE_THRESHOLD,
    RiskResult,
    consistency_risk,
    deadline_risk,
    estimation_risk,
    project_risk,
    scheduling_risk,
    task_risk,
    workload_risk,
)

if TYPE_CHECKING:
    from app.models.risk import Risk
    from app.models.user import User
    from app.repositories.risk import RiskRepository
    from app.services.risk.recommendation import Recommendation, RecommendationService

__all__ = [
    "BLOCKED_SCAN_LIMIT",
    "DEADLINE_SCAN_LIMIT",
    "ENTITY_ACCOUNT",
    "ENTITY_PROJECT",
    "ENTITY_TASK",
    "MAX_DEADLINE_TASKS",
    "MAX_PROJECT_RISKS",
    "MAX_RECOMMENDATION_RISKS",
    "MAX_TASK_RISKS",
    "PROJECT_TARGET_PAGE_SIZE",
    "PROJECT_TARGET_SCAN_LIMIT",
    "RESCHEDULE_SCAN_LIMIT",
    "RESOLVE_SWEEP_LIMIT",
    "RiskDetectionService",
]

logger = get_logger(__name__)

#: The three entity types a risk can point at. Spelled as constants because they
#: are a third of the partial unique index ``uq_risks_live_identity``, so a typo
#: here is a deduplication failure rather than a display bug — two account-level
#: risks of the same type would refuse to coexist exactly when they should.
ENTITY_TASK = "task"
ENTITY_PROJECT = "project"
ENTITY_ACCOUNT = "account"

#: How many open tasks are read when looking for deadline pressure. The page is
#: ordered by due date ascending, so the cap drops the *furthest* deadlines —
#: the ones the scoring function would have graded lowest — rather than an
#: arbitrary slice of the set.
DEADLINE_SCAN_LIMIT = 200
#: How many deadline risks one pass will write. A user with two hundred open
#: deadlines does not have a Risk Center, they have a scheduling problem that the
#: nearest twenty rows already describe.
MAX_DEADLINE_TASKS = 25
#: How many project risks one pass will write, keeping the highest scores. The
#: remainder are still measured, and they are **deferred**: their live risks are
#: left alone by the resolution sweep and the count is reported, because a cap on
#: what is written is not evidence that what was left out has gone away.
MAX_PROJECT_RISKS = 25
#: How many risks are handed to the recommendation generator. Recommendations
#: hang off the risks that raised them, so the ones beyond this cap are the ones
#: a user could not act on today anyway.
MAX_RECOMMENDATION_RISKS = 25
#: Upper bound on the blocked-task scan, and on the deadline scan below. Both are
#: a floor rather than a total when the cap bites, and a floor understates a
#: risk rather than inventing one — see
#: :meth:`RiskDetectionService._blocked_tasks`. What it may no longer do is let
#: the sweep close a task risk whose task was never in the page: a truncated
#: candidate scan now marks ``RiskType.TASK`` unexamined for the pass.
BLOCKED_SCAN_LIMIT = 500
#: Upper bound on the reschedule read, ordered so the tasks with the most moves
#: are the ones inside the cap. A floor again, and the same direction of error as
#: the blocked scan: fewer candidates measured rather than more risks raised.
RESCHEDULE_SCAN_LIMIT = 500
#: How many task risks one pass will write, keeping the highest scores, exactly
#: as :data:`MAX_PROJECT_RISKS` caps the project detector. An account with more
#: blocked tasks than that has one task to unblock at a time, and the ones
#: beyond the cap stay in the account — as **live risks the sweep will not
#: close**, which is the whole difference between deferring a risk and resolving
#: one.
MAX_TASK_RISKS = 25
#: Upper bound on the resolution sweep. A user returning after a long absence may
#: have more stale risks than one transaction should close, and closing them in
#: bounded batches means the history of each is written rather than the whole
#: backlog being flipped in a single statement nobody can attribute to a run.
RESOLVE_SWEEP_LIMIT = 200
#: Page size for the project target-date read, and the ceiling on that read
#: overall. Both are here because :class:`ProjectAnalyticsRead` is unbounded — it
#: returns a row for every project the owner has — so a single page of target
#: dates silently described only the first hundred projects and every project
#: past the page lost its ``days_to_deadline``. A page whose consequence is not
#: reported is a measurement gap wearing the shape of a measurement, so the
#: ceiling is stated and the overflow goes onto the run summary.
PROJECT_TARGET_PAGE_SIZE = 100
PROJECT_TARGET_SCAN_LIMIT = 1000

#: The statuses that make a task "open" for the purposes of deadline pressure. A
#: cancelled task is not at risk of anything, and including it would raise a risk
#: about work the user deliberately dropped.
_OPEN_TASK_STATUSES = frozenset(
    {TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value}
)

#: A session the user cancelled is not in the plan any more, so it is not evidence
#: of a conflict in the plan.
_CANCELLED_SESSION = "cancelled"

#: Tolerance, in minutes either side of an availability edge, for a session that
#: touches the edge exactly. Compared as minutes-since-midnight rather than by
#: offsetting a ``datetime.time``, which arithmetic with a ``timedelta`` does not
#: support.
_AVAILABILITY_TOLERANCE_MINUTES = 1


@dataclass(frozen=True, slots=True)
class _Finding:
    """One detector's answer, before it is judged for whether it can be stored.

    Carries no title or description: those are built only for a result that will
    actually be written, so a detector that could not judge never produces the
    string that would have described its non-answer.
    """

    result: RiskResult
    entity_type: str | None
    entity_id: uuid.UUID | None
    #: Carried only to stamp the matching ``activity_events`` row. The feed groups
    #: by project and task, and a risk about a task whose event cannot be
    #: attributed to that task is harder to follow than the risk itself.
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None

    @property
    def identity(self) -> tuple[str, str | None, uuid.UUID | None]:
        """The three columns of the live-identity index, plus the risk type.

        This is exactly what is handed to
        :meth:`RiskRepository.list_stale_live_risks` as ``seen``, so the
        repository's exclusion query has to compare NULLs as NULLs rather than
        against a value no row can ever equal.
        """
        return (self.result.risk_type.value, self.entity_type, self.entity_id)


@dataclass(frozen=True, slots=True)
class _TaskCandidate:
    """One open task the task-level detector measured, and what it measured.

    The union of the two scans that can find a candidate: the blocked-task read
    and the reschedule read. They are merged rather than scored separately
    because the two conditions are not exclusive — a task that is blocked *and*
    has been moved three times is one task with two conditions, and scoring it
    twice would give the Risk Center two rows to dismiss where the engine has
    two facts about one problem.
    """

    task: Task
    #: All-time ``TASK_RESCHEDULED`` events for this task, from the merged read.
    #: Zero when only the blocked scan found it.
    reschedules: int


class RiskDetectionService:
    """Run the risk detectors, persist what is real, and close what is not.

    One instance per request. It holds no state between passes, so two concurrent
    evaluations of the same account are both correct: the second pass's upsert
    collides into the first pass's row through the partial unique index rather
    than through anything this class remembers.
    """

    def __init__(
        self,
        metrics: AnalyticsRepository,
        analytics: AnalyticsService,
        tasks: TaskRepository,
        projects: ProjectRepository,
        risks: RiskRepository,
        activity: ActivityService | None = None,
        recommendations: RecommendationService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        Args:
            metrics: The analytics repository, read for the two inputs Phase 6
                computes but does not publish: the raw estimation pairs, and the
                flat session rows behind the scheduling signals. Kept separate
                from ``analytics`` deliberately — a figure Phase 6 *does* publish
                is read through the service, never around it.
            analytics: The Phase 6 service. Every aggregate a detector consumes
                comes from here, so no figure in this module is derived twice.
            tasks: Task persistence, for the open-deadline and blocked scans.
            projects: Project persistence, for ``target_date``. The project
                roll-up carries every other project signal but not that one,
                because a target date is a column somebody typed rather than an
                aggregate of the project's tasks.
            risks: Risk persistence, including the deduplicating upsert and the
                stale sweep.
            activity: The history sink. ``None`` runs the lifecycle rules with
                nowhere to record them, which is a real mode for exercising the
                rules in isolation but is not a mode the API layer uses — see
                :func:`app.api.deps.get_activity_service`.
            recommendations: The recommendation generator, called once at the end
                of a pass with the risks that survived. ``None`` means this
                deployment raises risks without suggestions; the pass still
                reconciles correctly and reports zero recommendations.
            settings: Resolved from the environment when not supplied. Supplies
                the analytics range ceiling the evaluation window is checked
                against.
        """
        self.metrics = metrics
        self.analytics = analytics
        self.tasks = tasks
        self.projects = projects
        self.risks = risks
        self.activity = activity
        self.recommendations = recommendations
        self.settings = settings or get_settings()

    # -- Public surface ------------------------------------------------------

    async def evaluate(self, *, owner: User, today: date, window_days: int = 14) -> EvaluationRead:
        """Run one detection pass and return its summary.

        The pass is a single read of the recorded data followed by a single
        reconciliation. The only write that is not an upsert is the resolution
        sweep, and that moves a row the detectors no longer agree with into
        ``resolved`` — the brief's "if the underlying condition disappears, mark
        the risk as resolved", without which the Risk Center only ever grows.

        Args:
            owner: The account being evaluated. Every read and every write is
                scoped to them, in a predicate rather than a post-filter.
            today: The date the pass is anchored on. Passed in rather than read
                from a clock so a caller can re-run a past window and get the
                same answer, and so the database's date — the one every deadline
                in the system is judged against — stays the caller's decision.
            window_days: Length of the evaluation window, and of the planning
                horizon that follows it. The trailing window feeds the detectors
                that describe what happened; the horizon feeds the scheduling
                detector, which is about a plan the user has not lived yet.

        Returns:
            The run's summary, which is also persisted as one ``risk_evaluations``
            row. ``reason_if_not_evaluated`` is populated whenever any detector
            declined to judge or measured nothing, so an empty Risk Center can
            always be explained.

        Raises:
            ValidationError: If ``window_days`` is not positive, or the window it
                implies is wider than ``Settings.analytics_max_range_days``. The
                analytics reads enforce the same ceiling; checking it here turns a
                window mistake into one error at the boundary instead of six
                identical ones from the middle of the pass.

        **The pass is observable.** ``risk_detection_started`` and
        ``risk_detection_completed`` bracket every run with the window and the
        counts, and ``risk_detection_failed`` carries the traceback if a read, an
        upsert or the sweep raises. This pass resolves risks as well as creating
        them, so a silent half-finished run is the one outcome an operator must
        be able to find.
        """
        self._check_window(window_days)
        pass_started = perf_counter()
        log_event(
            logger,
            logging.INFO,
            "risk_detection_started",
            owner_id=str(owner.id),
            today=today.isoformat(),
            window_days=window_days,
        )
        try:
            summary = await self._run_pass(owner=owner, today=today, window_days=window_days)
        except Exception:
            # Counts and the exception *type*, never a risk title, a description
            # or the evidence behind one — all of which are one user's work.
            log_event(
                logger,
                logging.ERROR,
                "risk_detection_failed",
                exc_info=True,
                owner_id=str(owner.id),
                today=today.isoformat(),
                window_days=window_days,
                elapsed_ms=round((perf_counter() - pass_started) * 1000, 3),
            )
            raise
        log_event(
            logger,
            logging.INFO,
            "risk_detection_completed",
            owner_id=str(owner.id),
            window_days=window_days,
            risks_found=summary.risks_found,
            risks_created=summary.risks_created,
            risks_updated=summary.risks_updated,
            risks_resolved=summary.risks_resolved,
            recommendations_created=summary.recommendations_created,
            duration_ms=summary.duration_ms,
        )
        return summary

    async def _run_pass(self, *, owner: User, today: date, window_days: int) -> EvaluationRead:
        """The pass itself, with no logging and no window validation.

        Split from :meth:`evaluate` so the start/finish/failure lines wrap exactly
        the work and nothing else: ``_check_window`` stays outside them, because a
        window wider than the analytics ceiling is a caller's 422 rather than a
        detection pass that failed.

        Args:
            owner: The account being evaluated, and the owner of every row written.
            today: The date the pass is anchored on.
            window_days: Length of the evaluation window and the planning horizon.

        Returns:
            The run's summary, also persisted as one ``risk_evaluations`` row.
        """
        started = perf_counter()
        now = await self._now()
        window_start = today - timedelta(days=window_days - 1)
        previous_start, previous_end = _previous_window(window_start, today)
        # The horizon exists because the scheduling detector reads a plan, and a
        # plan for next week is not in the rows describing last week. It is the
        # same length as the trailing window so that "over the next fortnight" is
        # one number to a reader rather than two.
        horizon_end = today + timedelta(days=window_days)

        context = await self._gather(
            owner=owner,
            today=today,
            window_start=window_start,
            window_end=today,
            previous_start=previous_start,
            previous_end=previous_end,
            horizon_end=horizon_end,
        )
        findings, deferred, unassessable = await self._detect(context=context, today=today, now=now)
        kept, unavailable, zeros = self._partition(findings)

        written: list[Risk] = []
        created = updated = 0
        for finding in kept:
            title, description = _wording(finding)
            row, was_created = await self.risks.upsert_risk(
                owner.id,
                risk_type=finding.result.risk_type.value,
                severity=_severity_value(finding),
                score=_scored_value(finding),
                title=title,
                description=description,
                evidence=_evidence_payload(finding.result),
                evidence_strength=finding.result.evidence_strength.value,
                entity_type=finding.entity_type,
                entity_id=finding.entity_id,
                metadata=finding.result.metadata,
            )
            written.append(row)
            created += int(was_created)
            updated += int(not was_created)
            await self._record_event(
                ActivityEvent.RISK_DETECTED if was_created else ActivityEvent.RISK_UPDATED,
                owner=owner,
                row=row,
                finding=finding,
            )

        resolved = await self._resolve_stale(
            owner=owner,
            seen=[finding.identity for finding in kept],
            deferred=[
                finding.identity
                for finding in deferred
                if finding.result.available and finding.result.score
            ],
            unexamined=context.unexamined,
            now=now,
        )
        recommendations = await self._recommend(owner=owner, risks=written)
        duration_ms = int((perf_counter() - started) * 1000)

        await self.risks.record_evaluation(
            owner.id,
            window_start=datetime.combine(window_start, time.min, tzinfo=UTC),
            window_end=datetime.combine(today, time.max, tzinfo=UTC),
            risks_found=len(kept),
            risks_created=created,
            risks_updated=updated,
            risks_resolved=resolved,
            by_severity=_tally(kept, severity=True),
            by_type=_tally(kept, severity=False),
            recommendations_created=len(recommendations),
            duration_ms=duration_ms,
        )

        return self._summary(
            now=now,
            window_start=window_start,
            window_end=today,
            findings=findings,
            kept=kept,
            created=created,
            updated=updated,
            resolved=resolved,
            recommendations=len(recommendations),
            unavailable=unavailable,
            unassessable=unassessable,
            zeros=zeros,
            duration_ms=duration_ms,
        )

    def _check_window(self, window_days: int) -> None:
        """Reject a window the analytics reads could not honour anyway."""
        if window_days < 1:
            raise ValidationError("window_days must be at least 1.")
        ceiling = self.settings.analytics_max_range_days
        if window_days > ceiling:
            raise ValidationError(f"An evaluation window may span at most {ceiling} days.")

    # -- Gathering -----------------------------------------------------------

    async def _gather(
        self,
        *,
        owner: User,
        today: date,
        window_start: date,
        window_end: date,
        previous_start: date,
        previous_end: date,
        horizon_end: date,
    ) -> _Context:
        """Read everything the seven detectors consume, once.

        Every analytics read shares the one window and each is asked for exactly
        once. ``overview``, ``focus`` and ``time_distribution`` are deliberately
        **not** read: ``overview`` is those same computations re-joined plus a
        daily series, and neither the recomputation nor the series is an input to
        any detector, so calling it would double the pass's round trips to obtain
        nothing; ``focus`` and ``time_distribution`` describe sessions that were
        run, which is not what any of the seven detectors asks about.

        Args:
            owner: The account being evaluated.
            today: The anchor date.
            window_start: First day of the trailing window.
            window_end: Last day of the trailing window.
            previous_start: First day of the equally long window before it, for
                the consistency comparison.
            previous_end: Last day of that earlier window.
            horizon_end: Last day of the planning horizon.

        Returns:
            The reads, plus the per-row detail Phase 6 does not publish.
        """
        start, end = window_start, window_end
        workload = await self.analytics.workload(owner=owner, start=start, end=end)
        deadlines = await self.analytics.deadlines(owner=owner, start=start, end=end)
        estimation = await self.analytics.estimation(owner=owner, start=start, end=end)
        consistency = await self.analytics.consistency(owner=owner, start=start, end=end)
        previous = await self.analytics.consistency(
            owner=owner, start=previous_start, end=previous_end
        )
        projects = await self.analytics.project_analytics(owner=owner, start=start, end=end)
        task_analytics = await self.analytics.task_analytics(owner=owner, start=start, end=end)

        # One flat session read spanning the trailing window *and* the horizon.
        # Overlap, out-of-hours bookings and after-deadline sessions are all
        # faults in a plan, and a session that starts after its task's due date is
        # one whether that due date is behind the pass or ahead of it.
        session_rows = await self.metrics.work_session_rows(
            owner.id, start=window_start, end=horizon_end
        )
        availability = await self.metrics.availability_windows(owner.id)

        # The account roll-up answers "is there anything to scan for?" first, so
        # a pass over an account with no blocked work does not pay for the scan.
        blocked_rows, blocked_unseen = await self._blocked_tasks(
            owner.id, wanted=bool(task_analytics.blocked_tasks)
        )
        open_due, unestimated, deadline_unseen = await self._open_due_tasks(
            owner.id, horizon_end, wanted=bool(task_analytics.open_tasks)
        )
        targets, targets_unseen = await self._project_targets(owner.id)
        rescheduled, reschedule_truncated = await self._rescheduled_tasks(
            owner.id, wanted=bool(task_analytics.open_tasks)
        )
        task_candidates = _merge_task_candidates(blocked_rows, rescheduled)

        return _Context(
            owner_id=owner.id,
            today=today,
            window_start=window_start,
            window_end=window_end,
            horizon_end=horizon_end,
            window_label=f"the {(end - start).days + 1} days to {end:%d %b %Y}",
            workload=workload,
            deadlines=deadlines,
            estimation=estimation,
            consistency=consistency,
            previous_consistency=previous,
            projects=projects,
            blocked_by_project=_blocked_counts_by_project(blocked_rows),
            blocked_unseen=blocked_unseen,
            open_due=open_due,
            unestimated_open_due=unestimated,
            deadline_unseen=deadline_unseen,
            project_targets=targets,
            project_targets_unseen=targets_unseen,
            task_candidates=task_candidates,
            reschedule_truncated=reschedule_truncated,
            session_rows=session_rows,
            availability=availability,
            unexamined=_unexamined_types(
                deadline_unseen=deadline_unseen,
                targets_unseen=targets_unseen,
                task_scan_truncated=bool(blocked_unseen) or reschedule_truncated,
            ),
        )

    async def _now(self) -> datetime:
        """The database's clock, as an aware instant.

        Never ``datetime.now()``: a host whose clock drifts from the server's
        would stamp ``evaluated_at`` and ``resolved_at`` in the wrong hour, and
        the whole point of those columns is that "when did this close" stays
        answerable later from a single source. Same reasoning and same read as
        :meth:`AnalyticsService._today`.
        """
        value = await self.metrics.session.scalar(select(func.now()))
        return value if isinstance(value, datetime) else datetime.now(UTC)

    async def _open_due_tasks(
        self, owner_id: uuid.UUID, horizon_end: date, *, wanted: bool
    ) -> tuple[list[Task], int]:
        """Open, estimated tasks due on or before the horizon, soonest first.

        Read as one page ordered by due date ascending and narrowed in Python,
        which is what :meth:`AnalyticsService._top_overdue` does for the same
        reason: ``due_before`` is the half of the predicate an index can serve,
        and the open-status filter has no equivalent parameter because the
        repository takes one status rather than a set of them.

        The ordering is what makes :data:`DEADLINE_SCAN_LIMIT` cheap: truncating a
        list sorted by due date drops the *latest* deadlines, which are the ones
        :func:`~app.services.risk.scoring.deadline_risk` would have graded lowest
        anyway. It does not make it safe. A dropped candidate is a task this pass
        did not judge, so the third value says how many there were and
        :data:`DEADLINE_SCAN_LIMIT` being reached marks ``RiskType.DEADLINE``
        unexamined for the sweep — a deadline risk behind the page is left alone
        rather than closed on the strength of a pass that never saw it.

        Tasks with no estimate are **excluded** and counted separately. With no
        estimate the remaining work is unknown rather than zero, and scoring one
        against a remaining of zero would produce a measured "no work
        outstanding" — a claim about a task nobody estimated.

        Args:
            owner_id: The account to scan.
            horizon_end: Last day of the planning horizon.
            wanted: False when the account roll-up says there are no open tasks
                at all, in which case the query is skipped.

        Returns:
            ``(tasks, skipped_unestimated, candidates_outside_the_page)``.
        """
        if not wanted:
            return [], 0, 0
        rows, total = await self.tasks.list_for_user(
            owner_id,
            limit=DEADLINE_SCAN_LIMIT,
            offset=0,
            due_before=horizon_end,
            sort="due_date",
            order="asc",
        )
        assessable: list[Task] = []
        unestimated = 0
        for task in rows:
            if task.status not in _OPEN_TASK_STATUSES or task.due_date is None:
                continue
            if not task.estimated_minutes:
                unestimated += 1
                continue
            assessable.append(task)
        return assessable, unestimated, max(0, total - len(rows))

    async def _blocked_tasks(self, owner_id: uuid.UUID, *, wanted: bool) -> tuple[list[Task], int]:
        """Every open task in the blocked status, and how many were not read.

        Read once and used twice: the rows are the project detector's blocked
        counts *and* the task detector's candidates, and reading them separately
        would be two queries whose answers could disagree by one row.

        The second value is the number of blocked tasks outside the scanned page,
        and it is reported rather than hidden because a floor that a reader cannot
        see is just a wrong total. Understating the count is the safe direction:
        it lowers a project risk rather than raising one on nothing. The cap sits
        far above any plausible blocked backlog for that to bite — and when it
        does bite, a non-zero value is also what tells the rest of the pass that
        ``RiskType.TASK`` was not judged in full, so the blocked task risks behind
        the page are not swept shut.
        """
        if not wanted:
            return [], 0
        rows, total = await self.tasks.list_for_user(
            owner_id, limit=BLOCKED_SCAN_LIMIT, offset=0, status=TaskStatus.BLOCKED.value
        )
        return list(rows), max(0, total - len(rows))

    async def _rescheduled_tasks(
        self, owner_id: uuid.UUID, *, wanted: bool
    ) -> tuple[list[tuple[Task, int]], bool]:
        """Open tasks with at least three recorded reschedules, most first.

        The one read this module assembles rather than delegates, and the reason
        is the shape of the answer: a reschedule is a due-date edit with no column
        anywhere in the schema, so
        :meth:`~app.repositories.analytics.AnalyticsRepository.task_event_count` is
        the only reader of the fact and it takes one task at a time. Asking it per
        candidate task would make the pass's cost depend on how many open tasks the
        user has, which is precisely the cost the other detectors avoid by asking
        one question per shape of data.

        Both halves of the predicate are in the query rather than in Python. The
        owner is one because ownership is a predicate in every statement this
        module writes, and the open status is one because a task completed a
        month ago is not at risk of anything today — its four reschedules are
        history, not a condition, and counting them would raise a risk against a
        finished row. The ``HAVING`` does the same job for the threshold: a task
        with two moves never becomes a candidate, so nothing downstream has to
        decide whether it is interesting.

        Returns:
            ``(candidates, maybe_more)``. A full page is the only evidence this
            read has that another candidate exists, so ``maybe_more`` is reported
            as a flag rather than as a count: this query cannot count what it
            chose not to fetch, and a fabricated zero there would be the same
            "confident zero" failure the blocked scan's own count avoids by
            reading the repository's total.
        """
        if not wanted:
            return [], False
        result = await self.metrics.session.execute(
            select(Task, func.count())
            .select_from(ActivityLog)
            .join(Task, Task.id == ActivityLog.task_id)
            .where(
                ActivityLog.user_id == owner_id,
                ActivityLog.event_type == ActivityEvent.TASK_RESCHEDULED.value,
                Task.owner_id == owner_id,
                Task.status.in_(_OPEN_TASK_STATUSES),
            )
            .group_by(Task.id)
            .having(func.count() >= TASK_RESCHEDULE_THRESHOLD)
            .order_by(func.count().desc(), Task.id.asc())
            .limit(RESCHEDULE_SCAN_LIMIT)
        )
        candidates = [(task, int(count)) for task, count in result.all()]
        return candidates, len(candidates) >= RESCHEDULE_SCAN_LIMIT

    async def _project_targets(self, owner_id: uuid.UUID) -> tuple[dict[uuid.UUID, date], int]:
        """The ``project_id -> target_date`` map, and how many projects it missed.

        Read from ``projects`` rather than derived: a target date is a column
        somebody typed, and :class:`ProjectAnalyticsRead` cannot carry one
        without Phase 6 publishing a field it deliberately does not.

        **Paged to the end rather than to the first hundred rows.** The roll-up
        this read is paired with is unbounded — it carries a row for every project
        the owner has, including a project with no tasks — so a single page of
        target dates silently described only the newest hundred projects and
        every project past the page scored as though it had no target date at
        all: ``days_to_deadline`` ``None``, the deadline sub-signal absent, and
        up to 20 points of the score gone. The same project measured at 38 became
        2 purely by being created early enough, which is a scoring result
        determined by insertion order rather than by the project.

        A short page ends the walk, so the ordinary case is still one round trip.
        The scan limit is the same kind of ceiling as the candidate scans above
        and its consequence is reported the same way, and — because a project
        whose target date was never read measures a *lower* score — the limit also
        marks ``RiskType.PROJECT`` unexamined, so a risk that is still true
        because of its target date is not closed for want of the one column that
        would have said so.

        Returns:
            ``(targets, projects_beyond_the_scan)``.
        """
        targets: dict[uuid.UUID, date] = {}
        read = 0
        total = 0
        while read < PROJECT_TARGET_SCAN_LIMIT:
            rows, total = await self.projects.list_for_user(
                owner_id, limit=PROJECT_TARGET_PAGE_SIZE, offset=read
            )
            for row in rows:
                if row.target_date is not None:
                    targets[row.id] = row.target_date
            read += len(rows)
            if len(rows) < PROJECT_TARGET_PAGE_SIZE:
                break
        return targets, max(0, total - read)

    # -- Detection -----------------------------------------------------------

    async def _detect(
        self, *, context: _Context, today: date, now: datetime
    ) -> tuple[list[_Finding], list[tuple[str, str]]]:
        """Run the seven detectors over one gathered context.

        Every detector runs whether or not its answer is likely to be useful.
        Running the two that can decline — workload with no declared capacity,
        consistency with no earlier window — is what produces the "not enough
        data" sentence the UI needs; skipping them *because* they might decline
        would be indistinguishable from there being nothing to say.

        Args:
            context: Everything :meth:`_gather` read.
            today: The anchor date, for days-to-target.
            now: The anchor instant, for hours-to-deadline.

        Returns:
            ``(findings, deferred, unassessable)``. ``deferred`` are findings a
            write cap kept out of this pass — measured, scoring above zero, and
            **not** written, which is a different thing from not found.
            ``unassessable`` are inputs that could not be assembled at all and so
            never reached a scoring function. The partition into persisted and
            dropped happens in :meth:`_partition`, so both rules live in one
            place.
        """
        findings: list[_Finding] = []
        deferred: list[_Finding] = []
        deadlines, held = self._detect_deadlines(context=context, now=now)
        findings.extend(deadlines)
        deferred.extend(held)
        findings.append(self._detect_workload(context))
        findings.append(await self._detect_estimation(context))
        findings.append(self._detect_consistency(context))
        projects, held = self._detect_projects(context=context, today=today)
        findings.extend(projects)
        deferred.extend(held)
        findings.append(self._detect_scheduling(context))
        tasks, held = self._detect_tasks(context)
        findings.extend(tasks)
        deferred.extend(held)
        return findings, deferred, self._coverage_notes(context, deferred)

    def _coverage_notes(self, context: _Context, deferred: list[_Finding]) -> list[tuple[str, str]]:
        """What this pass could not look at, as ``(label, reason)`` pairs.

        A detector with no rows to read never reaches its scoring function —
        there is nothing to pass it. Those gaps are named here so that "no
        deadline risks" can be distinguished from "no task carries both a due
        date and an estimate", which are very different sentences and both of
        which otherwise look like an empty Risk Center.

        The capped detectors are named here too, for the same reason and more
        sharply: a bound on what a pass writes is invisible in its output, and a
        reader who cannot see it reads the rows that are missing as rows that do
        not exist. Each sentence therefore says what was left out **and** that
        the left-out risks were left open rather than closed.
        """
        notes: list[tuple[str, str]] = []
        if not context.open_due:
            detail = "no open task carries both a due date inside this window and an estimate"
            if context.unestimated_open_due:
                detail = (
                    f"{context.unestimated_open_due} open task(s) have a due date inside this "
                    "window but no estimate, and were not assessed"
                )
            notes.append(("Deadline", f"Not assessed: {detail}."))
        if not context.task_candidates:
            notes.append(
                (
                    "Task",
                    "Not assessed: no open task is recorded as blocked or has "
                    f"{TASK_RESCHEDULE_THRESHOLD} or more reschedules in its history.",
                )
            )
        if context.blocked_unseen:
            notes.append(
                (
                    "Project",
                    f"{context.blocked_unseen} blocked task(s) were beyond the scan limit and "
                    "are not counted in any project's blocked total.",
                )
            )
        if context.deadline_unseen:
            notes.append(
                (
                    "Deadline",
                    f"{context.deadline_unseen} open task(s) due inside this window were beyond "
                    f"the scan limit of {DEADLINE_SCAN_LIMIT} and were not assessed, so open "
                    "deadline risks were left as they are.",
                )
            )
        if context.blocked_unseen or context.reschedule_truncated:
            reason = (
                f"{context.blocked_unseen} blocked task(s) were beyond the scan limit"
                if context.blocked_unseen
                else f"the reschedule scan reached its limit of {RESCHEDULE_SCAN_LIMIT} candidates"
            )
            notes.append(
                (
                    "Task",
                    f"Not assessed in full: {reason}, so open task risks were left as they are.",
                )
            )
        if context.project_targets_unseen:
            notes.append(
                (
                    "Project",
                    f"{context.project_targets_unseen} project(s) were beyond the target-date scan "
                    f"limit of {PROJECT_TARGET_SCAN_LIMIT} and are scored without their target "
                    "date, so open project risks were left as they are.",
                )
            )
        for finding in deferred:
            if not finding.result.available or not finding.result.score:
                continue
            notes.append(
                (
                    _detector_label(finding.result.risk_type),
                    f"{finding.result.score} more {finding.result.risk_type.value} risk(s) "
                    "measured above zero were beyond the cap this pass writes, and their open "
                    "risks were left as they are.",
                )
            )
        return _dedupe(notes)

    def _detect_deadlines(
        self, *, context: _Context, now: datetime
    ) -> tuple[list[_Finding], list[_Finding]]:
        """One deadline risk per open, estimated task due inside the horizon.

        Per task rather than per account, because a deadline is a fact about a
        task: one figure for two tasks with different due dates would either miss
        the near one or alarm about the far one, and the recommendation raised
        from the risk has to name a task to act on.

        ``historical_completion_rate`` is the fraction of decided tasks finished
        by their due date, read from Phase 6 rather than recomputed, and ``None``
        — a neutral multiplier rather than a pessimistic guess — whenever Phase 6
        had nothing to decide.

        Returns:
            ``(written, deferred)``. The page is already ordered by due date, so
            the nearest ``MAX_DEADLINE_TASKS`` deadlines are the ones kept, and
            what is left is returned rather than discarded — a task past the cap
            whose risk is still live must not be closed for the crime of not being
            one of the twenty-five nearest.
        """
        booked = _booked_minutes_by_task(context.session_rows)
        # Divided by 100 here, and the reason is worth stating because getting it
        # wrong is invisible: `DeadlineAdherenceRead.adherence_rate` is a
        # **percentage** (Phase 6 documents it as "on_time / (on_time + late),
        # as a percentage"), while `deadline_risk` expects a 0-1 fraction and
        # clamps to that range. Handing it the percentage directly meant every
        # adherence at or above 50% clamped to exactly 1.0, so an account that
        # finishes 100% on time and one that finishes 55% got the identical
        # multiplier — the completion-rate input silently did nothing over the
        # entire range where it would have mattered most.
        adherence = (
            context.deadlines.adherence_rate / 100.0 if context.deadlines.available else None
        )
        findings: list[_Finding] = []
        for task in context.open_due:
            if task.due_date is None:  # pragma: no cover - filtered in _open_due_tasks
                continue
            estimated = int(task.estimated_minutes or 0)
            result = deadline_risk(
                remaining_minutes=max(0, estimated - int(task.actual_minutes)),
                available_minutes=_booked_before_due(booked.get(task.id, ()), task.due_date),
                deadline_in_hours=_hours_until(task.due_date, now),
                priority=task.priority,
                historical_completion_rate=adherence,
                title=task.title,
            )
            findings.append(
                _Finding(
                    result=result,
                    entity_type=ENTITY_TASK,
                    entity_id=task.id,
                    project_id=task.project_id,
                    task_id=task.id,
                )
            )
        return _split_by_cap(findings, MAX_DEADLINE_TASKS)

    def _detect_workload(self, context: _Context) -> _Finding:
        """Scheduled minutes against declared availability, over the window.

        Reads :attr:`WorkloadRead.scheduled_minutes` and
        :attr:`WorkloadRead.available_minutes` verbatim. The ratio is recomputed
        by the scoring function rather than read from
        :attr:`WorkloadRead.workload_ratio` because that function is the single
        definition of the ratio for the risk engine, and two definitions of it
        would eventually disagree by a rounding step.
        """
        return _Finding(
            result=workload_risk(
                scheduled_minutes=int(context.workload.scheduled_minutes),
                available_minutes=context.workload.available_minutes,
                window_label=context.window_label,
            ),
            entity_type=ENTITY_ACCOUNT,
            entity_id=None,
        )

    async def _detect_estimation(self, context: _Context) -> _Finding:
        """Systematic over- or under-estimation across the window's completions.

        The raw pairs come from the analytics repository because
        :class:`EstimationAccuracyRead` publishes the *mean* error of the same
        rows and the detector needs the distribution: an average of 40% hides a
        user who was spot-on nine times in ten and 400% once, and those are two
        different histories to plan around. Phase 6's read still decides whether
        the rows are worth reading at all — an account with no completed
        comparison does not pay for the query.
        """
        pairs: list[tuple[int, int]] = []
        if context.estimation.available:
            rows = await self.metrics.completed_pairs_in_range(
                context.owner_id, start=context.window_start, end=context.window_end
            )
            pairs = [
                (int(estimated), int(actual))
                for _task_id, estimated, actual, _due, _completed in rows
                if estimated
            ]
        return _Finding(
            result=estimation_risk(pairs=pairs),
            entity_type=ENTITY_ACCOUNT,
            entity_id=None,
        )

    def _detect_consistency(self, context: _Context) -> _Finding:
        """Recorded active days against the equally long earlier window.

        Both windows go through the Phase 6 service, so "active" means the same
        thing on both sides — one day carrying at least one recorded event —
        rather than the earlier one being computed here by a second definition.
        """
        current, previous = context.consistency, context.previous_consistency
        return _Finding(
            result=consistency_risk(
                active_days=int(current.active_days),
                window_days=int(current.window_days),
                previous_active_days=int(previous.active_days) if previous.available else None,
                previous_window_days=int(previous.window_days),
            ),
            entity_type=ENTITY_ACCOUNT,
            entity_id=None,
        )

    def _detect_projects(
        self, *, context: _Context, today: date
    ) -> tuple[list[_Finding], list[_Finding]]:
        """One project risk per project, from the Phase 6 project roll-up.

        Overdue, remaining and velocity are read from
        :class:`ProjectAnalyticsRead`; the blocked count and the target date are
        the two inputs that roll-up does not carry and are read alongside it.
        Required velocity is derived rather than read because Phase 6 publishes
        the observed pace only: finishing the remaining work in the days to the
        target date is arithmetic on two figures that already exist, not a new
        measurement.

        Returns:
            ``(written, deferred)``. Highest score first, so the cap keeps the
            projects a user would act on, and by id within a score so two passes
            over the same data defer the same rows. What the cap leaves out is
            **deferred, not resolved**: those projects are measured, they are
            reported on the run summary, and their live risks are left alone by
            the sweep — a project still carrying three overdue tasks is not at
            risk of nothing merely because it was the twenty-sixth.
        """
        findings: list[_Finding] = []
        for project in context.projects:
            target = context.project_targets.get(project.project_id)
            days_to_deadline = None if target is None else (target - today).days
            remaining = int(project.remaining_tasks)
            result = project_risk(
                overdue_tasks=int(project.overdue_tasks),
                blocked_tasks=int(context.blocked_by_project.get(project.project_id, 0)),
                days_to_deadline=days_to_deadline,
                remaining_tasks=remaining,
                required_velocity=_required_velocity(remaining, days_to_deadline),
                recent_velocity=project.velocity_tasks_per_week,
                project_name=project.name,
            )
            findings.append(
                _Finding(
                    result=result,
                    entity_type=ENTITY_PROJECT,
                    entity_id=project.project_id,
                    project_id=project.project_id,
                )
            )
        # Highest first, so the cap keeps the projects a user would act on, and by
        # id within a score so two passes over the same data defer the same rows.
        findings.sort(key=lambda finding: (-(finding.result.score or 0), str(finding.entity_id)))
        return _split_by_cap(findings, MAX_PROJECT_RISKS)

    def _detect_scheduling(self, context: _Context) -> _Finding:
        """Faults in the plan itself: overlap, out-of-hours, after-deadline, runs.

        Account-level by construction. Every signal is a property of the schedule
        rather than of one task or project, and a per-row risk would multiply a
        single double-booking into a row per participant.

        A run of back-to-back sessions is reported as a count and nothing more.
        The scoring module documents why, and it is worth repeating where the
        number is assembled: a system that infers fatigue from a calendar is
        making a claim it has no data for, and the brief rules that out.

        ``sessions_considered`` is the one input this detector supplies and the
        scoring function could not have derived. Everything else is a count the
        scoring module computed from the same rows; whether there *were* rows is
        a fact only this layer holds, and it is what separates "the plan has no
        conflicts" from "there is no plan". Without it the detector was
        unconditionally available on four zeroes, which made the run summary's
        ``evaluated`` flag permanently true and let a pass over an empty account
        claim to have measured something.
        """
        signals = _scheduling_signals(
            session_rows=context.session_rows,
            availability=context.availability,
            open_due=context.open_due,
        )
        return _Finding(
            result=scheduling_risk(
                overlapping_sessions=signals["overlapping_sessions"],
                outside_availability_sessions=signals["outside_availability_sessions"],
                sessions_after_deadline=signals["sessions_after_deadline"],
                longest_consecutive_run=signals["longest_consecutive_run"],
                sessions_considered=signals["sessions_considered"],
                window_label=context.window_label,
            ),
            entity_type=ENTITY_ACCOUNT,
            entity_id=None,
        )

    def _detect_tasks(self, context: _Context) -> tuple[list[_Finding], list[_Finding]]:
        """One task risk per task that is blocked, moved repeatedly, or both.

        The only detector scoped to a single row, and the reason it exists
        separately from the project detector is that the project roll-up answers
        a different question with the same words. "This project has three blocked
        tasks" is a fact about the project; "this task is blocked" is the fact a
        suggestion can act on, and the row it needs is the one to attach to.

        A candidate always scores above zero, and that is why the reschedule read
        filters on the threshold rather than passing every moved task through:
        the candidates are exactly the tasks with a condition to report, and the
        accounts that have none say so through the coverage note rather than
        through a column of measured zeros.

        Returns:
            ``(written, deferred)``, and the deferral is the point of writing it
            this way. Thirty blocked tasks in one account is an ordinary morning,
            and truncating to twenty-five used to mean the other five were
            reported as having gone away on every single pass — which is how five
            of them could never come back, because a resolved risk is terminal
            until the condition returns and the row is recreated from scratch.
        """
        findings: list[_Finding] = []
        for candidate in context.task_candidates:
            task = candidate.task
            findings.append(
                _Finding(
                    result=task_risk(
                        blocked=task.status == TaskStatus.BLOCKED.value,
                        reschedules=candidate.reschedules,
                        title=task.title,
                    ),
                    entity_type=ENTITY_TASK,
                    entity_id=task.id,
                    project_id=task.project_id,
                    task_id=task.id,
                )
            )
        # Highest first, so the cap keeps the tasks a user would act on, and by
        # id within a score so two passes over the same data produce the same
        # twenty-five rows in the same order.
        findings.sort(key=lambda finding: (-(finding.result.score or 0), str(finding.entity_id)))
        return _split_by_cap(findings, MAX_TASK_RISKS)

    # -- Judging what to persist --------------------------------------------

    def _partition(
        self, findings: list[_Finding]
    ) -> tuple[list[_Finding], list[tuple[str, str]], list[str]]:
        """Split one pass's findings into what is stored and what is only measured.

        The two rules that decide this are the two the module docstring is about,
        and they are applied in one place so that neither can be applied to some
        detectors and forgotten for the rest.

        Returns:
            ``(kept, unavailable, measured_zero)``. ``kept`` are the findings to
            upsert; the other two are label/reason pairs and labels destined for
            the run summary.
        """
        kept: list[_Finding] = []
        unavailable: list[tuple[str, str]] = []
        zeros: list[str] = []

        for finding in findings:
            label = _detector_label(finding.result.risk_type)
            if not finding.result.available:
                reason = finding.result.reason_if_unavailable or NOT_ENOUGH_DATA
                unavailable.append((label, reason))
            elif not finding.result.score:
                zeros.append(label)
            else:
                kept.append(finding)

        return kept, _dedupe(unavailable), sorted(set(zeros))

    # -- Writing -------------------------------------------------------------

    async def _resolve_stale(
        self,
        *,
        owner: User,
        seen: list[tuple[str, str | None, uuid.UUID | None]],
        deferred: list[tuple[str, str | None, uuid.UUID | None]],
        unexamined: frozenset[RiskType],
        now: datetime,
    ) -> int:
        """Close every live risk this pass did not re-detect **and could judge**.

        This is the step that makes a risk *mean* something. A risk the user
        neither dismissed nor acted on is still sitting in the Risk Center a
        month later unless something notices it is no longer true, and the only
        thing that can notice is a detector that ran and did not agree. Without
        this sweep the table is append-only and the user's only way to clear it
        is by hand.

        ``seen`` is the identity of every risk written this pass, and a detector
        that measured zero is deliberately absent from it, because a zero is the
        condition having gone away, which is exactly what should close the old
        row.

        The two further arguments exist because absence of a finding is not
        evidence unless the pass was in a position to produce one, and the
        activity event this writes says "the condition behind it was not detected
        in this evaluation" — a sentence that is only true when NEXUS looked.

        * ``deferred`` are the identities a **write cap** kept out. The condition
          was measured and scored above zero; there was simply no room in the
          twenty-five rows this pass writes. Closing them would report a live
          condition as a condition that ceased.
        * ``unexamined`` are the risk types whose **candidate read** stopped at
          its limit, so this pass cannot say anything at all about whether their
          conditions are gone. Those risks are left open, which is the safe
          direction: a risk that outlives its condition is one row the next
          untruncated pass closes, and the run summary says why in the meantime.

        The risks' recommendations are expired in the same step, because a
        suggestion attached to a risk that no longer exists is moot, and "moot" is
        a more useful thing to record than "old".

        Args:
            owner: The account being swept.
            seen: Identities written this pass.
            deferred: Identities measured this pass but kept out by a write cap.
            unexamined: Risk types this pass could not judge in full.
            now: The instant to stamp ``resolved_at`` with.

        Returns:
            How many live risks were closed.
        """
        stale = [
            row
            for row in await self.risks.list_stale_live_risks(
                owner.id, seen=set(seen) | set(deferred), limit=RESOLVE_SWEEP_LIMIT
            )
            if row.risk_type not in unexamined
        ]
        if not stale:
            return 0
        resolved_ids = [row.id for row in stale]
        for row in stale:
            await self.risks.transition_risk(
                owner.id, row.id, status=RiskStatus.RESOLVED.value, resolved_at=now
            )
            if self.activity is not None:
                await self.activity.record(
                    ActivityEvent.RISK_RESOLVED,
                    user_id=owner.id,
                    project_id=row.entity_id if row.entity_type == ENTITY_PROJECT else None,
                    task_id=row.entity_id if row.entity_type == ENTITY_TASK else None,
                    metadata={
                        "risk_id": str(row.id),
                        "risk_type": row.risk_type,
                        "severity": row.severity,
                        "reason": "the condition behind it was not detected in this evaluation",
                    },
                )
        await self.risks.expire_recommendations_for_risks(owner.id, resolved_ids)
        return len(stale)

    async def _recommend(self, *, owner: User, risks: list[Risk]) -> list[Recommendation]:
        """Hand this pass's risks to the recommendation generator.

        A no-op when no generator is wired: the risk reconciliation is complete
        without it, and a deployment without suggestions should get a correct
        Risk Center rather than an error.
        """
        if self.recommendations is None or not risks:
            return []
        return await self.recommendations.generate(
            owner=owner, risks=risks[:MAX_RECOMMENDATION_RISKS]
        )

    async def _record_event(
        self, event: ActivityEvent, *, owner: User, row: Risk, finding: _Finding
    ) -> None:
        """Write one risk lifecycle event, when a sink is wired.

        Metadata is ids and numbers only. A task title is the user's own words and
        belongs on the row the event points at; duplicating it into a feed nobody
        asked for is how a summary view ends up quoting stale text.
        """
        if self.activity is None:
            return
        await self.activity.record(
            event,
            user_id=owner.id,
            project_id=finding.project_id,
            task_id=finding.task_id,
            metadata={
                "risk_id": str(row.id),
                "risk_type": row.risk_type,
                "severity": row.severity,
                "score": int(row.score),
                "evidence_strength": row.evidence_strength,
            },
        )

    # -- Summary -------------------------------------------------------------

    def _summary(
        self,
        *,
        now: datetime,
        window_start: date,
        window_end: date,
        findings: list[_Finding],
        kept: list[_Finding],
        created: int,
        updated: int,
        resolved: int,
        recommendations: int,
        unavailable: list[tuple[str, str]],
        unassessable: list[tuple[str, str]],
        zeros: list[str],
        duration_ms: int,
    ) -> EvaluationRead:
        """Assemble the run summary returned to the caller.

        The persisted row is written by :meth:`evaluate` from the same counters,
        so the response a client reads and the row a later run compares against
        are the same numbers by construction rather than by discipline.

        ``evaluated`` is true when at least one detector could judge something. A
        pass in which every detector declined has run correctly and produced
        nothing, and saying so is the difference between "you have no risks" and
        "there is not enough recorded yet to say".
        """
        notes = [f"{label}: {reason}" for label, reason in (*unavailable, *unassessable)]
        if zeros:
            notes.append(f"{', '.join(zeros)}: measured no risk in this window.")
        return EvaluationRead(
            evaluated_at=now,
            risks_found=len(kept),
            risks_created=created,
            risks_updated=updated,
            risks_resolved=resolved,
            by_severity=_tally(kept, severity=True),
            by_type=_tally(kept, severity=False),
            recommendations_created=recommendations,
            duration_ms=duration_ms,
            window_start=datetime.combine(window_start, time.min, tzinfo=UTC),
            window_end=datetime.combine(window_end, time.max, tzinfo=UTC),
            evaluated=any(finding.result.available for finding in findings),
            reason_if_not_evaluated="; ".join(notes) or None,
        )


# ---------------------------------------------------------------------------
# The gathered context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Context:
    """Everything one pass read, in the shape the detectors want it.

    Grouping the reads into one object rather than passing fifteen arguments to
    every detector is what makes each detector a pure function of its context:
    ``_detect_workload(context)`` reads the workload figure and nothing else, and
    a reader checking that can see it on one line.
    """

    owner_id: uuid.UUID
    today: date
    window_start: date
    window_end: date
    horizon_end: date
    window_label: str
    workload: WorkloadRead
    deadlines: DeadlineAdherenceRead
    estimation: EstimationAccuracyRead
    consistency: ConsistencyRead
    previous_consistency: ConsistencyRead
    projects: list[ProjectAnalyticsRead]
    blocked_by_project: dict[uuid.UUID, int]
    blocked_unseen: int
    open_due: list[Task]
    unestimated_open_due: int
    #: Open tasks due inside the horizon that sat beyond :data:`DEADLINE_SCAN_LIMIT`.
    deadline_unseen: int
    project_targets: dict[uuid.UUID, date]
    #: Projects beyond :data:`PROJECT_TARGET_SCAN_LIMIT`, whose target date this
    #: pass therefore did not read.
    project_targets_unseen: int
    #: Open tasks the task-level detector has something to say about: the union
    #: of the blocked scan and the reschedule scan.
    task_candidates: list[_TaskCandidate]
    #: Whether the reschedule read filled its page, which is the only evidence it
    #: has that a candidate it did not return exists.
    reschedule_truncated: bool
    session_rows: list[tuple[Any, ...]]
    availability: list[tuple[int, time, time]]
    #: Risk types this pass could not judge in full, and therefore must not close
    #: a live risk of. Derived from the counts above rather than set at the call
    #: sites, so "did we look at everything?" is one question with one answer.
    unexamined: frozenset[RiskType]


def _unexamined_types(
    *, deadline_unseen: int, targets_unseen: int, task_scan_truncated: bool
) -> frozenset[RiskType]:
    """The risk types this pass was not in a position to judge.

    The three candidate reads that can stop short of their set — the deadline
    page, the blocked page, the reschedule page — plus the target-date read, whose
    shortfall does not remove a project from the roll-up but grades it against a
    deadline of ``None``. All four are reported on the run summary as well; this
    set is the half that has to reach the *sweep*, because a risk this pass could
    not judge must not be closed on the strength of a pass that never saw it.

    Account-level types are absent by construction: each of those detectors reads
    one aggregate for the whole window, so there is no per-row set to fall short
    of.
    """
    unexamined: set[RiskType] = set()
    if deadline_unseen:
        unexamined.add(RiskType.DEADLINE)
    if task_scan_truncated:
        unexamined.add(RiskType.TASK)
    if targets_unseen:
        unexamined.add(RiskType.PROJECT)
    return frozenset(unexamined)


def _split_by_cap(findings: list[_Finding], cap: int) -> tuple[list[_Finding], list[_Finding]]:
    """The first ``cap`` findings, and the ones the cap holds back.

    Both halves are returned because both matter and they are opposites: the
    first is what the pass writes, the second is what the pass must **not** treat
    as absent. A caller that discarded the second would be saying "this
    condition was not detected" about a condition it had just scored, which is
    the one inference this module must never make.
    """
    return findings[:cap], findings[cap:]


# ---------------------------------------------------------------------------
# Small pure helpers over the gathered rows
# ---------------------------------------------------------------------------


def _previous_window(start: date, end: date) -> tuple[date, date]:
    """The equal-length window immediately before ``[start, end]``.

    Equal length on purpose: the consistency detector divides active days by
    window days on both sides, so a 7-day window compared against a 28-day one
    would read a fall that is only arithmetic.
    """
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


def _blocked_counts_by_project(tasks: list[Task]) -> dict[uuid.UUID, int]:
    """``project_id -> blocked task count`` from an already-read page of rows.

    Kept here as a separate step so the blocked scan happens once. The project
    roll-up and the task detector both need the same rows, and two scans of the
    same table in one pass are two answers that can disagree by one row.
    """
    counts: dict[uuid.UUID, int] = {}
    for task in tasks:
        if task.project_id is not None:
            counts[task.project_id] = counts.get(task.project_id, 0) + 1
    return counts


def _merge_task_candidates(
    blocked: list[Task], rescheduled: list[tuple[Task, int]]
) -> list[_TaskCandidate]:
    """The union of the two task scans, one entry per task.

    Rescheduled first because that read is already ordered by how often the task
    has been moved, which is the order a reader would want; blocked-only tasks
    follow in the order the repository returned them. A task found by both scans
    appears once, carrying its count — it is one task with two conditions, and
    the two conditions are one score rather than two rows.
    """
    counts = {task.id: count for task, count in rescheduled}
    ordered = [task for task, _count in rescheduled]
    ordered.extend(task for task in blocked if task.id not in counts)
    return [_TaskCandidate(task=task, reschedules=counts.get(task.id, 0)) for task in ordered]


def _session_minutes(row: tuple[Any, ...]) -> int:
    """The minutes one session claims in the plan.

    Its own estimate when one was written, and otherwise the gap between its
    scheduled start and end. The estimate is preferred because it is the number
    the user typed; the duration is the fallback so a session the planner
    produced without one still contributes to the plan it was placed in.
    """
    estimated = row[7]
    if estimated:
        return int(estimated)
    start, end = row[3], row[4]
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() // 60))


def _booked_minutes_by_task(
    session_rows: list[tuple[Any, ...]],
) -> dict[uuid.UUID, list[tuple[date, int]]]:
    """``task_id -> [(day, minutes)]`` across every session read this pass.

    Kept as a list of ``(day, minutes)`` rather than one total because a task's
    due date cuts that list in two: only the part before the deadline counts as
    time booked *before* it. Cancelled sessions are excluded — they record that
    something was planned and then withdrawn, not part of the plan, and counting
    one would tell the user a deadline is better covered than it is.
    """
    booked: dict[uuid.UUID, list[tuple[date, int]]] = {}
    for row in session_rows:
        task_id, start = row[1], row[3]
        if task_id is None or start is None or row[9] == _CANCELLED_SESSION:
            continue
        day = start.astimezone(UTC).date()
        booked.setdefault(task_id, []).append((day, _session_minutes(row)))
    return booked


def _booked_before_due(sessions: list[tuple[date, int]], due: date) -> int:
    """Minutes booked against one task on or before its due date.

    A session after the deadline is the scheduling detector's *after-deadline*
    signal, and crediting it here as well would let the worst-planned task in the
    account look well covered.
    """
    return sum(minutes for day, minutes in sessions if day <= due)


def _hours_until(due: date, now: datetime) -> float:
    """Hours from ``now`` to the end of ``due``.

    End of day, because ``tasks.due_date`` is a date and the system decides
    on-time versus late at the same granularity
    (:meth:`AnalyticsService._deadline_result`). The consequence is that a task
    due *tomorrow* sits 24-48 hours out and falls in the 72-hour urgency band
    rather than the 24-hour one — which is correct, because tomorrow at 09:00
    really is more than a day away, and the brief's "due in 24 hours" example is
    a duration rather than a calendar word.

    Negative for a due date that has passed, which
    :func:`~app.services.risk.scoring.deadline_risk` scores as the worst case.
    """
    end_of_day = datetime.combine(due, time.max, tzinfo=UTC)
    return (end_of_day - now).total_seconds() / 3600.0


def _required_velocity(remaining_tasks: int, days_to_deadline: int | None) -> float | None:
    """Tasks per week needed to finish the remaining work by the target date.

    ``None`` when either input is missing. Not zero: a project with no target
    date has no pace it is required to keep, and a required velocity of zero would
    make the velocity signal read as "the required pace is nothing", which is a
    claim about nothing at all.
    """
    if days_to_deadline is None or days_to_deadline <= 0 or remaining_tasks <= 0:
        return None
    return remaining_tasks / (days_to_deadline / 7.0)


def _scheduling_signals(
    *,
    session_rows: list[tuple[Any, ...]],
    availability: list[tuple[int, time, time]],
    open_due: list[Task],
) -> dict[str, int]:
    """The four counts :func:`~app.services.risk.scoring.scheduling_risk` weighs.

    The fifth entry, ``sessions_considered``, is how many sessions those four
    counts were taken over.

    Computed in one pass over the already-fetched session rows rather than in
    four queries, because a plan's faults are not independent: two overlapping
    sessions are usually also two sessions outside availability, and asking the
    database the same question four times is how four answers drift apart.

    Overlap counts *sessions that start before the furthest end seen so far*,
    which is the number of sessions in conflict. A session clashing with two
    others counts once: the fault is the clash, and counting it twice would
    inflate a two-session mistake into a four-session one.

    ``sessions_considered`` is the size of ``ordered`` rather than the size of
    ``session_rows``, and that distinction is the whole reason the count is
    reported: a session with no start time is not a booking, and a cancelled one
    is not part of the plan any more, so neither belongs in the number the
    scoring function decides whether there *is* a plan.
    """
    windows: dict[int, list[tuple[time, time]]] = {}
    for weekday, window_start, window_end in availability:
        windows.setdefault(int(weekday), []).append((window_start, window_end))
    due_by_task = {task.id: task.due_date for task in open_due}

    ordered = sorted(
        (row for row in session_rows if row[3] is not None and row[9] != _CANCELLED_SESSION),
        key=lambda row: (row[3], row[0]),
    )

    overlapping = outside = after_deadline = 0
    longest_run = current_run = 0
    run_day: date | None = None
    previous_end: datetime | None = None
    furthest_end: datetime | None = None

    for row in ordered:
        start = row[3].astimezone(UTC)
        end = start if row[4] is None else row[4].astimezone(UTC)

        if furthest_end is not None and start < furthest_end:
            overlapping += 1
        furthest_end = end if furthest_end is None else max(furthest_end, end)

        if windows and not _within_availability(start, windows.get(start.weekday(), [])):
            outside += 1

        due = due_by_task.get(row[1])
        if due is not None and start.date() > due:
            after_deadline += 1

        back_to_back = (
            run_day is not None
            and previous_end is not None
            and start.date() == run_day
            and start <= previous_end
        )
        if back_to_back:
            current_run += 1
        else:
            run_day, current_run = start.date(), 1
        longest_run = max(longest_run, current_run)
        previous_end = end

    return {
        "overlapping_sessions": overlapping,
        "outside_availability_sessions": outside,
        "sessions_after_deadline": after_deadline,
        "longest_consecutive_run": longest_run,
        "sessions_considered": len(ordered),
    }


def _within_availability(moment: datetime, windows: list[tuple[time, time]]) -> bool:
    """Whether ``moment`` falls inside one of the declared windows for its weekday.

    Windows are read as same-day spans, matching
    :func:`app.services.analytics.service._minutes_between`, which is what Phase 6
    compares against when it computes declared availability. A window declared
    past midnight is therefore read as ending at midnight, and sessions in the
    small hours count as outside it. That is a limitation shared with the figure
    the workload detector is measured against, which is the right place for it to
    be wrong: two detectors disagreeing with each other would be the real bug,
    and this way they cannot. Weekdays are compared in UTC for the same reason —
    Phase 6 projects the availability windows onto days by that arithmetic.
    """
    if not windows:
        return True
    minute = moment.hour * 60 + moment.minute
    return any(
        _minute_of_day(window_start) - _AVAILABILITY_TOLERANCE_MINUTES
        <= minute
        <= _minute_of_day(window_end) + _AVAILABILITY_TOLERANCE_MINUTES
        for window_start, window_end in windows
    )


def _minute_of_day(value: time) -> int:
    """Minutes from midnight for a wall-clock time.

    The same projection :func:`app.services.analytics.service._minutes_between`
    applies to an availability window, which is what makes a session the workload
    detector and the scheduling detector disagree about the *same* declaration
    impossible.
    """
    return value.hour * 60 + value.minute + value.second // 60


def _dedupe(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Collapse repeated ``(label, reason)`` pairs, preserving order.

    The summary is a sentence a person reads, and a run over two hundred
    cold-start tasks that all declined for the same reason should say it once.
    """
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def _tally(findings: list[_Finding], *, severity: bool) -> dict[str, int]:
    """``{severity: count}`` or ``{risk_type: count}`` for the findings written.

    Counts the risks this pass wrote rather than the detectors that ran, because
    these two dictionaries are the Risk Center's header tally and a count of
    things that were deliberately not written would be a different number wearing
    the same name. Everything dropped is carried in the summary's reason sentence
    instead, which is where a person reads it.
    """
    counts: dict[str, int] = {}
    for finding in findings:
        key = _severity_value(finding) if severity else finding.result.risk_type.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _detector_label(risk_type: RiskType) -> str:
    """A human label for a detector, for the run summary.

    Capitalised and singular, because the sentence it appears in reads "Deadline:
    not enough data" rather than "the deadline detector reported".
    """
    return risk_type.value.capitalize()


def _severity_value(finding: _Finding) -> str:
    """The stored severity for a finding, from the scoring module's own ladder.

    Every finding reaching this point is available and scored, so the band always
    exists; the fallback is a defect guard rather than a decision, and it is the
    lowest band so that a defect can never invent urgency.
    """
    severity = finding.result.severity
    return severity.value if severity is not None else RiskSeverity.LOW.value


def _scored_value(finding: _Finding) -> int:
    """The integer stored on a finding about to be written.

    Named rather than written as ``finding.result.score or 0`` because that
    expression is precisely the coercion this module's rules forbid: it turns a
    detector's ``None`` — "I could not judge this" — into a stored ``0``, which is
    a measurement and reads as one. A detector that cannot judge must produce no
    row at all, with its reason on the run summary; a detector that measured zero
    must also produce no row, but it says so on the summary instead.

    Only findings that :meth:`RiskDetectionService._partition` judged available
    and non-zero reach this function, so ``None`` is unreachable rather than
    merely unlikely. It is raised rather than defaulted because a defect that got
    here would be writing a fabricated score into the training data, and a loud
    failure at that point is cheaper than a quiet zero nobody can trace.
    """
    score = finding.result.score
    if score is None:  # pragma: no cover - unreachable through _partition
        raise ValueError(
            "A risk cannot be stored from a detector that could not judge it; "
            f"{finding.result.risk_type.value} returned no score."
        )
    return int(score)


def _evidence_payload(result: RiskResult) -> list[dict[str, Any]]:
    """The evidence lines as the JSON the wire schema reads back.

    Structured rather than pre-formatted because ``RiskRead.evidence`` is a list
    of objects with a label, a detail and a contribution: storing a sentence
    would mean the API layer had to parse one back apart, and a parser that
    guesses at a delimiter is one copy edit away from attaching a contribution to
    the wrong line.
    """
    return [
        {
            "label": line.label,
            "detail": line.detail,
            "contribution": round(float(line.contribution), 2),
        }
        for line in result.evidence
    ]


# ---------------------------------------------------------------------------
# Wording — the complete vocabulary of things a user can be told
# ---------------------------------------------------------------------------


def _duration(minutes: float) -> str:
    """``240`` -> ``"4h"``, ``90`` -> ``"1h 30m"``, ``45`` -> ``"45m"``.

    Human units because a title is read by a person deciding whether to act; the
    arithmetic behind it stays in minutes. The same shapes as the scoring
    module's own formatter, repeated rather than imported so that the copy this
    module writes does not move when an evidence line's format is retuned.
    """
    total = round(minutes)
    if total <= 0:
        return "0m"
    hours, remainder = divmod(total, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _hours_phrase(hours: float) -> str:
    """``18`` -> ``"18 hours"``, ``72`` -> ``"3 days"``. A distance still ahead."""
    if hours >= 48:
        return f"{round(hours / 24)} days"
    if hours < 1:
        return "less than an hour"
    return f"{round(hours)} hours"


def _ago_phrase(hours: float) -> str:
    """The same distances, for one that has already passed."""
    return f"{_hours_phrase(abs(hours))} ago"


def _clip(text: str, limit: int) -> str:
    """Shorten a user-written title to fit the column, on a character boundary."""
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _clause(parts: list[str]) -> str:
    """Join factual fragments into one sentence.

    ``"a, b and c"``. A caller that reaches here has already decided the result
    was worth writing, and every branch that builds a list guarantees it is
    non-empty, so there is no empty-list case to render.
    """
    if len(parts) == 1:
        return f"{parts[0]}."
    return f"{', '.join(parts[:-1])} and {parts[-1]}."


def _deadline_wording(result: RiskResult) -> tuple[str, str]:
    """Title and description for a deadline risk on one task.

    States the size of the unbooked remainder and where the deadline sits. It
    does not say the task will be missed: the score says how exposed the task is,
    and being missed is a thing that has not happened yet.
    """
    meta = result.metadata
    name = _clip(str(meta.get("title") or "a task"), 120)
    remaining = int(meta.get("remaining_minutes") or 0)
    available = int(meta.get("available_minutes") or 0)
    hours = float(meta.get("deadline_in_hours") or 0.0)
    gap = max(0, remaining - available)

    if hours <= 0:
        return (
            f"Deadline passed on {name}",
            f"The due date passed {_ago_phrase(hours)} with about {_duration(gap)} of "
            f"{_duration(remaining)} of estimated work still outstanding.",
        )
    return (
        f"Deadline approaching for {name}",
        f"About {_duration(gap)} of the {_duration(remaining)} of estimated work remaining "
        f"has no time booked, and the due date is {_hours_phrase(hours)} away.",
    )


def _workload_wording(result: RiskResult) -> tuple[str, str]:
    """Title and description for scheduled work against declared capacity."""
    meta = result.metadata
    scheduled = int(meta.get("scheduled_minutes") or 0)
    available = int(meta.get("available_minutes") or 0)
    window = str(meta.get("window_label") or "this window")
    ratio = round(scheduled / available * 100) if available else 0
    return (
        f"Scheduled work is {ratio}% of declared availability",
        f"{_duration(scheduled)} of work is scheduled across {window} against "
        f"{_duration(available)} of declared availability, which is about "
        f"{_duration(max(0, scheduled - available))} beyond capacity.",
    )


def _estimation_wording(result: RiskResult) -> tuple[str, str]:
    """Title and description for a systematic estimation error.

    Names the direction as well as the size, because "running 30% long" and
    "running 30% short" are different findings, and a title carrying only the
    magnitude would leave the reader to work out which one this is.
    """
    meta = result.metadata
    overrun = float(meta.get("mean_overrun") or 0.0) * 100
    samples = int(meta.get("sample_count") or 0)
    direction = "longer" if overrun >= 0 else "shorter"
    return (
        f"Completed tasks ran {abs(overrun):.0f}% {direction} than estimated",
        f"Across {samples} completed task(s) in this window, the recorded duration ran "
        f"about {abs(overrun):.0f}% {direction} than the estimate it was given.",
    )


def _consistency_wording(result: RiskResult) -> tuple[str, str]:
    """Title and description for a fall in recorded activity.

    Describes the two counts and nothing else. Activity is a record of what was
    logged, and a detector that turned it into a claim about effort or motivation
    would be reading a cause into a row that does not carry one.
    """
    meta = result.metadata
    active = int(meta.get("active_days") or 0)
    window = int(meta.get("window_days") or 0)
    previous = int(meta.get("previous_active_days") or 0)
    previous_window = int(meta.get("previous_window_days") or 0)
    return (
        "Fewer days with recorded activity than the previous period",
        f"{active} of {window} day(s) carry recorded activity, against {previous} of "
        f"{previous_window} in the period immediately before.",
    )


def _project_wording(result: RiskResult) -> tuple[str, str]:
    """Title and description for one project's combined signals.

    Each contributing signal is named separately rather than blended into a
    verdict, so a reader can see which of the five is carrying the score and act
    on that one.
    """
    meta = result.metadata
    name = str(meta.get("project_name") or "A project")
    parts: list[str] = []
    overdue = int(meta.get("overdue_tasks") or 0)
    blocked = int(meta.get("blocked_tasks") or 0)
    remaining = int(meta.get("remaining_tasks") or 0)
    days = meta.get("days_to_deadline")
    if overdue:
        parts.append(f"{overdue} task(s) are past their due date")
    if blocked:
        parts.append(f"{blocked} task(s) are blocked")
    if days is not None and remaining > 0:
        parts.append(f"the target date is {int(days)} day(s) away")
    required, recent = meta.get("required_velocity"), meta.get("recent_velocity")
    if required and recent is not None and required > recent:
        parts.append(
            f"{float(recent):.1f} task(s) a week were completed against "
            f"{float(required):.1f} needed"
        )
    if remaining:
        parts.append(f"{remaining} task(s) are unfinished")
    return f"Open signals on {name}", f"Signals recorded for {name}: {_clause(parts)}"


def _scheduling_wording(result: RiskResult) -> tuple[str, str]:
    """Title and description for faults in the plan.

    The unbroken-run count is stated as a fact about the calendar. The scoring
    module explains why nothing is inferred from it, and that decision is not
    negotiable in the copy either: a system that reads fatigue off a schedule has
    no data for fatigue.
    """
    meta = result.metadata
    parts: list[str] = []
    overlaps = int(meta.get("overlapping_sessions") or 0)
    outside = int(meta.get("outside_availability_sessions") or 0)
    after_due = int(meta.get("sessions_after_deadline") or 0)
    longest_run = int(meta.get("longest_consecutive_run") or 0)
    if overlaps:
        parts.append(f"{overlaps} session(s) overlapping an earlier session")
    if outside:
        parts.append(f"{outside} session(s) starting outside declared availability")
    if after_due:
        parts.append(f"{after_due} session(s) starting after their task is due")
    if longest_run:
        parts.append(
            f"an unbroken run of {longest_run} back-to-back sessions with no gap between them"
        )
    return "Conflicts in the scheduled plan", f"The recorded plan has {_clause(parts)}"


def _task_wording(result: RiskResult) -> tuple[str, str]:
    """Title and description for a condition on one task.

    Names the condition and the count behind it, and stops there. A task moved
    four times is described as having been moved four times; whether it is too
    large, badly specified or repeatedly interrupted is a reading the rows do not
    carry, and the recommendation attached to this risk is where the engine offers
    one action instead of a diagnosis.

    Both conditions are named separately when both are present rather than blended
    into one sentence, so a reader can see which of the two is carrying the score.
    """
    meta = result.metadata
    name = _clip(str(meta.get("title") or "A task"), 120)
    blocked = bool(meta.get("blocked"))
    reschedules = int(meta.get("reschedules") or 0)
    threshold = int(meta.get("reschedule_threshold") or 0)

    if blocked and reschedules >= threshold:
        return (
            f"{name} is blocked and has been rescheduled {reschedules} times",
            f"The task is recorded in the blocked status and has been rescheduled "
            f"{reschedules} times in its recorded history.",
        )
    if blocked:
        return (
            f"{name} is blocked",
            "The task is recorded in the blocked status, so the work remaining on it "
            "cannot be placed until the block is cleared.",
        )
    return (
        f"{name} has been rescheduled {reschedules} times",
        f"{reschedules} reschedules are recorded against this task in its history, "
        f"and nothing else is recorded as blocking it.",
    )


#: Every risk type has a wording builder, and this table is what makes that
#: checkable: a new member of :class:`RiskType` without an entry here raises at
#: the point of use rather than producing an empty title in a stored row.
_WORDING: dict[RiskType, Callable[[RiskResult], tuple[str, str]]] = {
    RiskType.DEADLINE: _deadline_wording,
    RiskType.WORKLOAD: _workload_wording,
    RiskType.ESTIMATION: _estimation_wording,
    RiskType.CONSISTENCY: _consistency_wording,
    RiskType.PROJECT: _project_wording,
    RiskType.SCHEDULING: _scheduling_wording,
    RiskType.TASK: _task_wording,
}


def _wording(finding: _Finding) -> tuple[str, str]:
    """The ``(title, description)`` pair for a finding, in the user's language."""
    return _WORDING[finding.result.risk_type](finding.result)
