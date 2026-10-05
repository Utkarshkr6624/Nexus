"""What a stored risk turns into: a suggestion with a stated reason, raised once.

**Every test here requires a live PostgreSQL and is marked ``integration``.**

A detection pass produces *conditions*; this file is about the layer above it,
where a condition becomes a proposed action a person can take or decline. The
brief's requirement for that layer is unusually specific, and it is the reason
this file exists in this shape::

    WHAT / WHY / RELATED DATA / SUGGESTED ACTION

    Every recommendation should clearly state:
    - WHAT needs to happen
    - WHY (with specific data/reasoning)
    - RELATED DATA (task, project, metric)
    - SUGGESTED ACTION (what the user can do)

Four things are asserted as *properties of the engine* rather than as examples
of its output, because each is a way the whole design fails quietly:

* **A reason cannot be constructed without a figure.**
  :class:`~app.services.risk.recommendation.RecommendationDraft` raises on a
  reason carrying no digit, and the tests here assert both that the construction
  raises and that every one of the ten rules puts its *actual* numbers into the
  sentence — "3h", "127%", "4 of 7", "9.3", "14 day(s)", "35%". A reason that is a
  restatement of the title renders perfectly on a card, which is exactly why
  nobody would notice its absence without a check.
* **One suggestion per condition, refreshed rather than duplicated.**
  The brief forbids "hundreds of identical records", and the only thing that
  makes that true across runs is the repository's open-status partial index plus
  a refresh that replaces the wording. Both halves are asserted: the second pass
  creates nothing, and a suggestion whose numbers moved keeps its row and changes
  its text.
* **A declined suggestion can be raised again; an ignored one cannot.**
  The open set is ``new``/``viewed``, which is a product decision with a test
  either side of it, and the interesting direction is the one that is easy to get
  wrong in the permissive direction — a rejected row must not block a legitimate
  re-raise, and a viewed one must not either.
* **Opening a suggestion is not answering it.**
  ``responded_at`` is stamped by ``accept``/``reject``/``complete`` and by
  nothing else, because a system that trains on reads trains on a signal that
  says nothing.

Two regression guards sit at the end, both written against defects found while
this phase was being built rather than against the design:

* ``complete_blocked_task`` is twenty-one characters, and the column was once
  ``VARCHAR(16)``. The blocked-task rule fired correctly and then died in the
  database with the type silently truncated, so the test round-trips that
  recommendation through storage and reads the full string back.
* The account-level upsert path builds an ORM instance rather than going through
  ``INSERT ... ON CONFLICT``, and its ``metadata`` was being written under the
  column name instead of the ORM attribute name — an assignment SQLAlchemy
  accepts and never persists. Every account-level risk stored ``{}`` while its
  row-level sibling stored correctly. The metadata assertions here are therefore
  genuine database round trips through :func:`_stored`, not reads of the object
  the repository just handed back.
* A deadline sentence's date was rebuilt rather than read. ``detected_at`` (the
  instant the row was written) and ``deadline_in_hours`` (a gap measured at the
  start of the pass) are two different clocks, and adding one to the other names
  a day that is late whenever a pass begins before midnight and writes after it.
  The date now comes from the task's own ``due_date`` column, and the guard below
  moves ``detected_at`` a day forward so the disagreement is visible without
  having to run the suite at 23:59.

The fixtures build risks directly through
:meth:`~app.repositories.risk.RiskRepository.upsert_risk` rather than by running
the detection pass. A pass measures rows through six detectors, so a test of the
*copy* would be a test of the fixture data first; writing the metadata the rule
reads puts the numbers under the test's control and lets each rule's expected
sentence be derived by hand from them. Every expected string below was written
from the documented shape rather than copied from a run, so a wording change
shows up as a diff a reviewer can read.

Phase 9 — the last two rule groups
---------------------------------
The Phase 9 rules raise from the user's own learning record rather than from a
stored risk, so they are exercised through
:meth:`~app.services.risk.recommendation.RecommendationService.generate_learning`
and their fixtures are real ``learning_goals`` and ``skills`` rows rather than a
risk's ``metadata`` blob. Three properties are asserted there that could not be
asserted above, and each is a way this design fails quietly:

* **The same registry, the same dispatch, the same dedup.** The two names sit in
  :data:`~app.services.risk.recommendation.recommendation_rules` under the ``None``
  key, resolve through the same ``getattr``, and are written by the same
  ``_persist`` — so the open-status partial index, the refresh-instead-of-duplicate
  rule and the rejection-frees-the-row behaviour all apply without a word of
  Phase 9-specific code.
* **Priority is still derived, never chosen.** The two learning rules have no
  raising risk and so no severity to read a band off, which is exactly the opening
  a rule could take to name its own urgency. Both read theirs from a hand-written
  ladder over a figure they quote, and the structural test above checks their
  signatures alongside the other eight.
* **A missing figure is a reason to decline.** A goal with no target date, a
  skill nothing has ever been recorded against, and an account with no learning
  data at all each produce *nothing* — not a suggestion quoting ``0`` days or
  ``0%``, which is the fabricated-zero failure the phase forbids and the one a
  cold-start default would introduce without any test noticing until the copy
  read it.
"""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.activity import ActivityLog
from app.models.enums import (
    ActivityEvent,
    EvidenceStrength,
    LearningGoalStatus,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
    RiskSeverity,
    RiskStatus,
    RiskType,
    TaskStatus,
)
from app.models.learning import LearningGoal, Skill
from app.models.risk import Recommendation, Risk
from app.models.task import TaskDependency
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.learning import LearningRepository
from app.repositories.project import ProjectRepository
from app.repositories.risk import RiskRepository
from app.repositories.task import TaskRepository
from app.services.activity_service import ActivityService
from app.services.risk.recommendation import (
    DEFAULT_STALE_INACTIVE_DAYS,
    ENTITY_ACCOUNT,
    ENTITY_LEARNING_GOAL,
    ENTITY_PROJECT,
    ENTITY_SKILL,
    ENTITY_TASK,
    MIN_RESCHEDULES,
    RecommendationDraft,
    RecommendationService,
    recommendation_rules,
)
from tests.analytics_fixtures import DAY, AnalyticsSeed, at, register_user

pytestmark = pytest.mark.integration

#: The names every rule fixture is addressed by. One per rule in
#: :data:`~app.services.risk.recommendation.recommendation_rules`, in the order the
#: contracts' rule table lists them, so the parametrised sweeps below read as the
#: contracts' own table rather than as an arbitrary ordering.
RULE_KEYS = (
    "block_time",
    "review_deadline",
    "reduce_workload",
    "complete_blocked_task",
    "break_down_task",
    "update_estimate",
    "review_consistency",
    "review_project",
)

#: Registers the brief rules out. Word-level for the six that carry no meaning
#: outside the judgement, and phrase-level for ``behind``: the blocked-project
#: rule writes "re-scope the work behind it", which is a noun phrase about the
#: work underneath and not the progress judgement the contracts forbid
#: ("falling behind badly", section 4). A bare substring check would report that
#: sentence as a violation; matching the judgement states what is actually
#: banned. Narrowed deliberately and reported in the handoff rather than done
#: silently — and :data:`HEADLINE_PROHIBITIONS` below re-asserts the bare word on
#: the two fields the Risk Center renders as the headline and the justification.
NEUTRALITY_PROHIBITIONS = (
    "failing",
    "unproductive",
    "lazy",
    "burnout",
    "tired",
    "struggling",
    "falling behind",
    "fell behind",
    "behind on",
)

#: The stricter list, applied to ``title`` and ``reason`` alone. Neither field
#: contains "behind" in any rule, and the two are the ones a reader scans, so
#: there is nothing to narrow here and the word-level check is exact.
HEADLINE_PROHIBITIONS = (*NEUTRALITY_PROHIBITIONS, "behind")

#: The window label a workload fixture quotes. Spelled out rather than derived so
#: the expected sentence below can be written by hand.
WINDOW_LABEL = "the 14 days to 18 Jan 2026"

#: Every row the engine can be asked to move. One tuple rather than three
#: literals repeated per transition test, because "the three answering moves" is
#: itself the contract being asserted.
ANSWERING_MOVES = ("accept", "reject", "complete")

#: The status each answering move lands on and the event it must write. Both in
#: one table so a test can assert that a move wrote *its* event and nobody
#: else's, which is what "each records itself" actually means.
ANSWERING_OUTCOMES: dict[str, tuple[RecommendationStatus, ActivityEvent]] = {
    "accept": (RecommendationStatus.ACCEPTED, ActivityEvent.RECOMMENDATION_ACCEPTED),
    "reject": (RecommendationStatus.REJECTED, ActivityEvent.RECOMMENDATION_REJECTED),
    "complete": (RecommendationStatus.COMPLETED, ActivityEvent.RECOMMENDATION_COMPLETED),
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def owner(db_session: AsyncSession) -> User:
    """The account everything in this file belongs to."""
    return await register_user(db_session)


@pytest.fixture
def seed(db_session: AsyncSession, owner: User) -> AnalyticsSeed:
    """A row builder bound to this account, as the Phase 6 suites use it.

    Reused rather than replaced because the task, project and activity rows a
    rule needs are ordinary work-management rows: seeding them the way the
    application writes them is what makes a rule's read of the database a test
    of the rule rather than of a bespoke fixture.
    """
    return AnalyticsSeed(db_session, owner)


@pytest.fixture
def risks(db_session: AsyncSession) -> RiskRepository:
    """Risk persistence on the test session, for writing the risks a rule reads."""
    return RiskRepository(db_session)


@pytest.fixture
def service(db_session: AsyncSession) -> RecommendationService:
    """A recommendation service wired exactly as ``get_recommendation_service`` is.

    Including the activity sink. It is a collaborator rather than an optional
    extra here because the reschedule rule reads its count *through* it and
    declines to guess without one, so a fixture that left it out would silently
    stop testing one of the eight rules.
    """
    return RecommendationService(
        RiskRepository(db_session),
        TaskRepository(db_session),
        ProjectRepository(db_session),
        ActivityService(ActivityRepository(db_session)),
    )


@dataclass(frozen=True, slots=True)
class _RuleFixture:
    """One rule's trigger, the suggestions it produced, and the copy it must make.

    ``types`` is every suggestion the fixture's risks raised rather than the one
    the fixture exists for, because two rules legitimately fire on one risk — a
    project that is both blocked and under pressure raises two — and the tests
    below assert that whole set rather than only the one they were written for.
    """

    key: str
    risks: tuple[Risk, ...]
    rows: tuple[Recommendation, ...]
    types: tuple[RecommendationType, ...]
    primary: Recommendation
    entity_type: str
    entity_id: uuid.UUID | None
    title: str
    description: str
    reason: str
    #: Figures that must appear verbatim in ``reason``. Not a subset chosen for
    #: convenience: each one is a number the rule computed out of this fixture's
    #: metadata, so their absence means the rule is restating its title instead
    #: of citing the data behind it.
    figures: tuple[str, ...]


async def _store(
    risks: RiskRepository,
    owner: User,
    *,
    risk_type: RiskType,
    metadata: dict[str, Any],
    severity: RiskSeverity = RiskSeverity.HIGH,
    score: int = 60,
    title: str = "A condition worth acting on",
    evidence_strength: EvidenceStrength = EvidenceStrength.LOW,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> Risk:
    """Write one risk the way the detection pass would have written it.

    ``upsert_risk`` rather than a bare ``Risk(...)`` insert, so the row carries a
    real ``detected_at`` from the database clock and the deduplicating identity
    the rules' own lookups go through.
    """
    row, _created = await risks.upsert_risk(
        owner.id,
        risk_type=risk_type,
        severity=severity,
        score=score,
        title=title,
        description=f"{title}. Recorded by the detection pass.",
        evidence=[],
        evidence_strength=evidence_strength,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )
    return row


async def _build(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> _RuleFixture:
    """Run one rule's builder, generate from what it stored, and describe the result."""
    risk = await _BUILDERS[key](seed, risks, owner)
    rows = await service.generate(owner=owner, risks=[risk])
    fixture = _ASSERTERS[key](rows, risk)
    assert rows, f"the {key} fixture raised no suggestion at all"
    assert fixture.primary.id in {row.id for row in rows}
    return fixture


# ---------------------------------------------------------------------------
# The eight rule fixtures
# ---------------------------------------------------------------------------


async def _block_time_fixture(seed: AnalyticsSeed, risks: RiskRepository, owner: User) -> Risk:
    """Rule 1: a deadline with unbooked work left on it.

    300 minutes of estimated work, 120 booked, due the day after the pass. The
    gap is the number the suggestion exists to state, so it is chosen large
    enough that the title and the reason quote visibly different figures
    ("5h remaining", "2h booked", "3h with no time scheduled") and the test can
    tell a restatement from a citation.
    """
    project = await seed.project(name="Atlas data migration")
    task = await seed.task(project_id=project.id, title="Atlas data migration")
    return await _store(
        risks,
        owner,
        risk_type=RiskType.DEADLINE,
        severity=RiskSeverity.HIGH,
        score=60,
        evidence_strength=EvidenceStrength.MEDIUM,
        entity_type=ENTITY_TASK,
        entity_id=task.id,
        metadata={
            "title": "Atlas data migration",
            "remaining_minutes": 300,
            "available_minutes": 120,
            "deadline_in_hours": 24.0,
        },
    )


def _assert_block_time(rows: list[Recommendation], risk: Risk) -> _RuleFixture:
    """This fixture's task carries no due date, so the sentence names none.

    That is the honest branch and it is asserted whole: the rule reads the date
    off the task rather than rebuilding one from ``deadline_in_hours`` and
    ``detected_at``, so a task with no date recorded produces a pronoun and no
    invented day. The dated path is covered separately, by the regression guards
    below, which seed a real ``due_date``.
    """
    row = _only(rows, RecommendationType.BLOCK_TIME)
    return _RuleFixture(
        key="block_time",
        risks=(risk,),
        rows=tuple(rows),
        types=_types(rows),
        primary=row,
        entity_type=ENTITY_TASK,
        entity_id=risk.entity_id,
        title="Schedule another 3h for Atlas data migration before its due date",
        description=(
            "Add 3h of unscheduled work to Atlas data migration on or before its due "
            "date, so the time exists before the date it is needed."
        ),
        reason=(
            "5h of estimated work remains on Atlas data migration and 2h is booked "
            "before its due date, leaving 3h with no time scheduled. Recorded risk "
            "score 60 of 100 (high severity, medium evidence strength)."
        ),
        figures=("5h", "2h", "3h", "60 of 100"),
    )


async def _review_deadline_fixture(seed: AnalyticsSeed, risks: RiskRepository, owner: User) -> Risk:
    """Rule 2: the deadline is pressing but every remaining minute is booked.

    The complementary half of rule 1, and the reason the pair exists: with
    nothing unbooked, "schedule more time" is not an action anyone can take, and
    the only levers left are the date and the scope. The score is 40 — above the
    medium floor the rule gates on, but nowhere near urgent — so a fixture that
    quietly produced a ``BLOCK_TIME`` instead would show up as a type mismatch.
    """
    project = await seed.project(name="Ledger cutover")
    task = await seed.task(project_id=project.id, title="Ledger cutover")
    return await _store(
        risks,
        owner,
        risk_type=RiskType.DEADLINE,
        severity=RiskSeverity.MEDIUM,
        score=40,
        entity_type=ENTITY_TASK,
        entity_id=task.id,
        metadata={
            "title": "Ledger cutover",
            "remaining_minutes": 120,
            "available_minutes": 120,
            "deadline_in_hours": 48.0,
        },
    )


def _assert_review_deadline(rows: list[Recommendation], risk: Risk) -> _RuleFixture:
    """No due date on the task, so no date is named; the distance still is.

    "2 days" is quoted verbatim from the detector's own ``deadline_in_hours`` and
    is a duration rather than a calendar day, so the two clocks cannot disagree
    about it the way they could about a date. Asserting the sentence whole keeps
    that split honest: the date comes from a column or not at all, while the
    duration is the engine's own measurement and is repeated as given.
    """
    row = _only(rows, RecommendationType.REVIEW_DEADLINE)
    return _RuleFixture(
        key="review_deadline",
        risks=(risk,),
        rows=tuple(rows),
        types=_types(rows),
        primary=row,
        entity_type=ENTITY_TASK,
        entity_id=risk.entity_id,
        title="Check whether Ledger cutover is still achievable",
        description=(
            "Review whether Ledger cutover is still achievable against its due date, "
            "and move the date or the scope if it is not."
        ),
        reason=(
            "All 2h of the work remaining on Ledger cutover is already booked before "
            "its due date, which is 2 days away, so any slip has nowhere to go. "
            "Recorded risk score 40 of 100 (medium severity, low evidence strength)."
        ),
        figures=("2h", "2 days", "40 of 100"),
    )


async def _reduce_workload_fixture(seed: AnalyticsSeed, risks: RiskRepository, owner: User) -> Risk:
    """Rule 3: more planned work in the window than the window can hold.

    The contracts' own worked example (2280 scheduled against 1800 available,
    scoring 53), reused so the numbers in the expected sentence are the ones a
    reader can check against the brief. ``entity_id`` is null, which is what an
    account-level risk looks like in storage and what routes this suggestion
    through the repository's application-arbitrated upsert.
    """
    return await _store(
        risks,
        owner,
        risk_type=RiskType.WORKLOAD,
        severity=RiskSeverity.HIGH,
        score=53,
        evidence_strength=EvidenceStrength.MEDIUM,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        metadata={
            "scheduled_minutes": 2280,
            "available_minutes": 1800,
            "window_label": WINDOW_LABEL,
        },
    )


def _assert_reduce_workload(rows: list[Recommendation], risk: Risk) -> _RuleFixture:
    row = _only(rows, RecommendationType.REDUCE_WORKLOAD)
    return _RuleFixture(
        key="reduce_workload",
        risks=(risk,),
        rows=tuple(rows),
        types=_types(rows),
        primary=row,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        title="Move about 8h of planned work to later dates",
        description=(
            "Reschedule roughly 8h of the work planned across "
            f"{WINDOW_LABEL} to a later date, or take it off the plan."
        ),
        reason=(
            f"38h of work is scheduled across {WINDOW_LABEL} against 30h of declared "
            "availability, which is 127% of capacity and about 8h beyond it. Recorded "
            "risk score 53 of 100 (high severity, medium evidence strength)."
        ),
        figures=("38h", "30h", "8h", "127%", "53 of 100"),
    )


async def _complete_blocked_task_fixture(
    seed: AnalyticsSeed, risks: RiskRepository, owner: User
) -> Risk:
    """Rule 4: blocked work, raised on a project so the task branch is not used.

    A project risk carries the counts the detector measured, so the suggestion
    can be built from the risk alone. Two blocked tasks out of nine unfinished,
    one of them overdue — every figure the expected reason quotes is a key of
    ``metadata`` rather than a live read, which is the property that lets a
    stored risk stay explainable months later.
    """
    project = await seed.project(name="Atlas migration")
    return await _store(
        risks,
        owner,
        risk_type=RiskType.PROJECT,
        severity=RiskSeverity.HIGH,
        score=66,
        entity_type=ENTITY_PROJECT,
        entity_id=project.id,
        metadata={
            "project_name": "Atlas migration",
            "blocked_tasks": 2,
            "remaining_tasks": 9,
            "overdue_tasks": 1,
        },
    )


def _assert_complete_blocked_task(rows: list[Recommendation], risk: Risk) -> _RuleFixture:
    row = _only(rows, RecommendationType.COMPLETE_BLOCKED_TASK)
    return _RuleFixture(
        key="complete_blocked_task",
        risks=(risk,),
        rows=tuple(rows),
        types=_types(rows),
        primary=row,
        entity_type=ENTITY_PROJECT,
        entity_id=risk.entity_id,
        title="Resolve the blocked work in Atlas migration",
        description=(
            "Review the 2 blocked task(s) in Atlas migration and either clear the "
            "block or re-scope the work behind it."
        ),
        reason=(
            "Atlas migration has 2 task(s) recorded as blocked, of 9 still unfinished, "
            "and 1 of them past their due date. Recorded risk score 66 of 100 "
            "(high severity, low evidence strength)."
        ),
        figures=("2 task(s)", "9 still unfinished", "1 of them", "66 of 100"),
    )


async def _break_down_task_fixture(seed: AnalyticsSeed, risks: RiskRepository, owner: User) -> Risk:
    """Rule 5: a task moved at least three times.

    The count is the only input this rule takes from outside the risk, and it is
    read through the activity feed because reschedules are events and have no
    column. The fixture therefore writes exactly :data:`MIN_RESCHEDULES` events
    — no more, so that a fixture which accidentally raised the threshold would
    still pass here and fail on the boundary case below.
    """
    project = await seed.project(name="Reporting rebuild")
    task = await seed.task(
        project_id=project.id,
        title="Reporting rebuild",
        estimated_minutes=600,
        due_date=date(2026, 1, 12),
    )
    for index in range(MIN_RESCHEDULES):
        await seed.activity(
            ActivityEvent.TASK_RESCHEDULED, day=DAY, task_id=task.id, hour=9 + index
        )
    return await _store(
        risks,
        owner,
        risk_type=RiskType.TASK,
        severity=RiskSeverity.MEDIUM,
        score=48,
        entity_type=ENTITY_TASK,
        entity_id=task.id,
        metadata={"reschedule_count": MIN_RESCHEDULES},
    )


def _assert_break_down_task(rows: list[Recommendation], risk: Risk) -> _RuleFixture:
    row = _only(rows, RecommendationType.BREAK_DOWN_TASK)
    return _RuleFixture(
        key="break_down_task",
        risks=(risk,),
        rows=tuple(rows),
        types=_types(rows),
        primary=row,
        entity_type=ENTITY_TASK,
        entity_id=risk.entity_id,
        title="Break Reporting rebuild into smaller pieces",
        description=(
            "Split Reporting rebuild into tasks small enough to finish in one sitting, "
            "then schedule the first piece."
        ),
        reason=(
            "3 reschedules of Reporting rebuild are recorded in its history, against an "
            "estimate of 10h, with a due date of 12 Jan 2026. Recorded risk score 48 "
            "of 100 (medium severity, low evidence strength)."
        ),
        figures=("3 reschedules", "10h", "12 Jan 2026", "48 of 100"),
    )


async def _update_estimate_fixture(seed: AnalyticsSeed, risks: RiskRepository, owner: User) -> Risk:
    """Rule 6: a systematic over-run, measured across three completed tasks.

    ``mean_overrun`` is the contracts' own worked example (a mean of 0.6667 from
    the pairs (60,110) (90,150) (120,180)), carried through the same rounding the
    scoring module applies, so the "67%" in the expected sentence is the number
    the brief states rather than one this test chose.
    """
    return await _store(
        risks,
        owner,
        risk_type=RiskType.ESTIMATION,
        severity=RiskSeverity.CRITICAL,
        score=83,
        evidence_strength=EvidenceStrength.MEDIUM,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        metadata={
            "mean_overrun": 0.6667,
            "sample_count": 3,
            "under_estimation_count": 2,
            "over_estimation_count": 1,
        },
    )


def _assert_update_estimate(rows: list[Recommendation], risk: Risk) -> _RuleFixture:
    row = _only(rows, RecommendationType.UPDATE_ESTIMATE)
    return _RuleFixture(
        key="update_estimate",
        risks=(risk,),
        rows=tuple(rows),
        types=_types(rows),
        primary=row,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        title="Recent tasks ran about 67% over estimate",
        description=(
            "Add about 67% to the estimate on new work, or reduce the scope you are "
            "accepting at the current estimate."
        ),
        reason=(
            "Across 3 completed task(s) in this window, the recorded duration ran about "
            "67% over the estimate it was given, with 2 running long and 1 running "
            "short. Recorded risk score 83 of 100 (critical severity, medium evidence "
            "strength)."
        ),
        figures=("3 completed task(s)", "67%", "2 running long", "1 running short"),
    )


async def _review_consistency_fixture(
    seed: AnalyticsSeed, risks: RiskRepository, owner: User
) -> Risk:
    """Rule 7: fewer days recorded active than the equally long period before.

    The contracts' worked example — four of seven days against twelve of seven —
    which is a fall in *recorded rows* and nothing else. The suggestion's whole
    job here is to say that without implying anything about the person, which is
    why the expected wording is asserted in full and not just its type.
    """
    return await _store(
        risks,
        owner,
        risk_type=RiskType.CONSISTENCY,
        severity=RiskSeverity.HIGH,
        score=67,
        evidence_strength=EvidenceStrength.MEDIUM,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        metadata={
            "active_days": 4,
            "window_days": 7,
            "previous_active_days": 12,
            "previous_window_days": 7,
            "current_rate": 0.5714,
            "previous_rate": 1.7143,
        },
    )


def _assert_review_consistency(rows: list[Recommendation], risk: Risk) -> _RuleFixture:
    row = _only(rows, RecommendationType.REVIEW_PROJECT)
    return _RuleFixture(
        key="review_consistency",
        risks=(risk,),
        rows=tuple(rows),
        types=_types(rows),
        primary=row,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        title="Recorded activity is lower than the previous period",
        description=(
            "Look at which tasks carried the previous period's recorded activity and "
            "decide what is worth carrying into this one."
        ),
        reason=(
            "Recorded activity covers 4 of 7 day(s) in this window against 12 of 7 "
            "before it, which is 57% of days against 171%. Recorded risk score 67 of "
            "100 (high severity, medium evidence strength)."
        ),
        figures=("4 of 7", "12 of 7", "57%", "171%", "67 of 100"),
    )


async def _review_project_fixture(seed: AnalyticsSeed, risks: RiskRepository, owner: User) -> Risk:
    """Rule 8: combined pressure on one project, named signal by signal.

    ``blocked_tasks`` is zero here so that the blocked-work rule declines and this
    fixture isolates the review rule. Every contributing signal is spelled out
    because the score is a weighted sum of five: a reader who cannot see which
    of them is carrying the number cannot act on the number.
    """
    project = await seed.project(name="Platform cutover")
    return await _store(
        risks,
        owner,
        risk_type=RiskType.PROJECT,
        severity=RiskSeverity.HIGH,
        score=71,
        entity_type=ENTITY_PROJECT,
        entity_id=project.id,
        metadata={
            "project_name": "Platform cutover",
            "overdue_tasks": 4,
            "blocked_tasks": 0,
            "remaining_tasks": 12,
            "days_to_deadline": 9,
            "required_velocity": 9.3,
            "recent_velocity": 2.0,
        },
    )


def _assert_review_project(rows: list[Recommendation], risk: Risk) -> _RuleFixture:
    row = _only(rows, RecommendationType.REVIEW_PROJECT)
    return _RuleFixture(
        key="review_project",
        risks=(risk,),
        rows=tuple(rows),
        types=_types(rows),
        primary=row,
        entity_type=ENTITY_PROJECT,
        entity_id=risk.entity_id,
        title="Review the open signals on Platform cutover",
        description=(
            "Go through the open signals on Platform cutover — the overdue, blocked and "
            "remaining work listed against it — and decide which one to act on first."
        ),
        reason=(
            "Signals recorded for Platform cutover: 4 task(s) are past their due date, "
            "the target date is 9 day(s) away, 2.0 task(s) a week were completed "
            "against 9.3 needed and 12 task(s) are unfinished. Recorded risk score 71 "
            "of 100 (high severity, low evidence strength)."
        ),
        figures=("4 task(s)", "9 day(s)", "2.0 task(s) a week", "9.3 needed", "71 of 100"),
    )


#: Key -> (builder, asserter), one entry per rule. Addressed by the parametrised
#: sweeps below so that "every reason states its figures" and "no recommendation
#: uses a banned register" are driven from the same table the exact-copy
#: assertions are, and cannot drift apart from it.
_BUILDERS: dict[str, Callable[[AnalyticsSeed, RiskRepository, User], Awaitable[Risk]]] = {
    "block_time": _block_time_fixture,
    "review_deadline": _review_deadline_fixture,
    "reduce_workload": _reduce_workload_fixture,
    "complete_blocked_task": _complete_blocked_task_fixture,
    "break_down_task": _break_down_task_fixture,
    "update_estimate": _update_estimate_fixture,
    "review_consistency": _review_consistency_fixture,
    "review_project": _review_project_fixture,
}

_ASSERTERS: dict[str, Callable[[list[Recommendation], Risk], _RuleFixture]] = {
    "block_time": _assert_block_time,
    "review_deadline": _assert_review_deadline,
    "reduce_workload": _assert_reduce_workload,
    "complete_blocked_task": _assert_complete_blocked_task,
    "break_down_task": _assert_break_down_task,
    "update_estimate": _assert_update_estimate,
    "review_consistency": _assert_review_consistency,
    "review_project": _assert_review_project,
}


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------


def _types(rows: list[Recommendation]) -> tuple[RecommendationType, ...]:
    """The suggestion types raised, in the order they were written."""
    return tuple(RecommendationType(row.recommendation_type) for row in rows)


def _only(rows: list[Recommendation], wanted: RecommendationType) -> Recommendation:
    """The one row of this type among the raised ones.

    Raises:
        AssertionError: If the type was raised zero or more than once. A second
            row of the same type for one condition is the "hundreds of identical
            records" failure, so it has to be an exact count rather than a
            "first match wins" lookup.
    """
    matches = [row for row in rows if row.recommendation_type == wanted.value]
    assert len(matches) == 1, f"expected exactly one {wanted.value}, got {len(matches)}"
    return matches[0]


async def _stored(session: AsyncSession, recommendation_id: uuid.UUID) -> Recommendation:
    """Re-read one recommendation from the database rather than from the session.

    ``populate_existing`` overwrites whatever the identity map holds for this id
    with the row the statement just returned, which is what makes this a genuine
    round trip. Without it the object the repository just committed would answer,
    and the two regression guards at the end of this file — the truncated type
    column and the lost ``metadata`` — would both pass against an in-memory
    value the database never agreed to.

    ``session.expire_all()`` would be the obvious alternative and cannot be used
    on an ``AsyncSession``: expiring an object the caller still holds makes the
    next attribute access a lazy load, and a lazy load inside an assertion is a
    ``MissingGreenlet`` rather than a readable failure.
    """
    result = await session.execute(
        select(Recommendation)
        .where(Recommendation.id == recommendation_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _all_rows(session: AsyncSession, owner: User) -> list[Recommendation]:
    """Every stored recommendation for this account, oldest first."""
    result = await session.execute(
        select(Recommendation)
        .where(Recommendation.user_id == owner.id)
        .order_by(Recommendation.created_at.asc(), Recommendation.id.asc())
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def _events(
    session: AsyncSession, owner: User, event_type: ActivityEvent
) -> list[ActivityLog]:
    """Every activity event of one type this account has written."""
    result = await session.execute(
        select(ActivityLog)
        .where(
            ActivityLog.user_id == owner.id,
            ActivityLog.event_type == event_type.value,
        )
        .order_by(ActivityLog.created_at.asc())
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


def _prohibitions(text: str, words: tuple[str, ...] = NEUTRALITY_PROHIBITIONS) -> list[str]:
    """Which banned registers ``text`` uses, matched on word boundaries.

    Whole words rather than substrings so that a banned register is not reported
    by accident — ``struggling`` inside a longer identifier, or ``lazy`` inside
    a task title the user typed — while every actual use is still caught.
    """
    lowered = text.lower()
    return [
        word for word in words if re.search(rf"\b{re.escape(word)}\b", lowered)
    ]  # ---------------------------------------------------------------------------


# WHAT / WHY / RELATED DATA / SUGGESTED ACTION
# ---------------------------------------------------------------------------


def _bare_draft(**overrides: Any) -> RecommendationDraft:
    """A well-formed draft, for a test to break in one named way.

    Built through a helper rather than spelled eight times so that each test's
    only difference is the field it is asserting on — the property under test is
    that *one* missing invariant fails construction, and a helper carrying the
    other seven correctly is what makes that legible.
    """
    values: dict[str, Any] = {
        "recommendation_type": RecommendationType.BLOCK_TIME,
        "priority": RecommendationPriority.MEDIUM,
        "title": "Schedule another 3h for the migration",
        "description": "Add 3h of unscheduled work to the migration before its due date.",
        "reason": "3h of estimated work has no time booked against it.",
        "entity_type": ENTITY_TASK,
        "entity_id": None,
    }
    values.update(overrides)
    return RecommendationDraft(**values)


def _expected_types(key: str) -> tuple[RecommendationType, ...]:
    """Every type a rule fixture raises, in write order.

    Six fixtures raise exactly one; the blocked-project fixture raises two,
    because a project that is both blocked and under pressure genuinely carries
    two suggestions and the contracts' rule table registers both rules for
    ``RiskType.PROJECT``.
    """
    if key == "complete_blocked_task":
        return (RecommendationType.COMPLETE_BLOCKED_TASK, RecommendationType.REVIEW_PROJECT)
    return (
        {
            "block_time": RecommendationType.BLOCK_TIME,
            "review_deadline": RecommendationType.REVIEW_DEADLINE,
            "reduce_workload": RecommendationType.REDUCE_WORKLOAD,
            "break_down_task": RecommendationType.BREAK_DOWN_TASK,
            "update_estimate": RecommendationType.UPDATE_ESTIMATE,
            "review_consistency": RecommendationType.REVIEW_PROJECT,
            "review_project": RecommendationType.REVIEW_PROJECT,
        }[key],
    )


async def test_a_draft_cannot_be_built_from_a_reason_carrying_no_figure() -> None:
    """The brief's failure mode, made unconstructible.

    A bare imperative renders perfectly well on a card — there is no visual
    difference between "Schedule another 3h" with a reason and without one — so
    the guarantee has to live in construction rather than in review. The message
    is asserted too: a future reader who trips this needs to know which of the
    four required parts is missing, and "must state the figures behind it" says
    it.
    """
    with pytest.raises(ValueError, match="must state the figures behind it"):
        _bare_draft(reason="The schedule is not keeping up with the work.")


async def test_a_draft_cannot_be_built_from_a_blank_reason() -> None:
    """An empty reason fails for the same reason, with a different message."""
    with pytest.raises(ValueError, match="must state the figures behind it"):
        _bare_draft(reason="   ")


async def test_a_draft_cannot_be_built_from_a_blank_title() -> None:
    """WHAT is non-optional: a blank title is an empty card, not a suggestion."""
    with pytest.raises(ValueError, match="must carry a title"):
        _bare_draft(title="  ")


async def test_a_draft_cannot_be_built_from_a_blank_description() -> None:
    """SUGGESTED ACTION is non-optional for the same reason."""
    with pytest.raises(ValueError, match="must carry a description"):
        _bare_draft(description="")


async def test_a_draft_with_all_four_parts_is_constructible() -> None:
    """The check is a floor and not a wall — the conforming case must still work.

    Asserted rather than assumed, because a validator that rejected everything
    would pass every test above.
    """
    draft = _bare_draft()

    assert draft.recommendation_type is RecommendationType.BLOCK_TIME
    assert draft.reason == "3h of estimated work has no time booked against it."


@pytest.mark.parametrize("key", RULE_KEYS)
async def test_each_rule_fires_on_the_fixture_built_for_it(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """Every rule produces its own type, in its own copy, out of its own numbers.

    This is the (b) and (a) assertion in one place: the type says which rule
    fired, and the exact title/description/reason say what it said. Each expected
    sentence was written by hand from the fixture's metadata, so a rule that
    started restating its title, or dropped a figure it computed, shows up as a
    diff rather than as a type that still looks right.
    """
    fixture = await _build(key, seed, risks, owner, service)

    assert fixture.types == _expected_types(key)
    assert fixture.primary.title == fixture.title
    assert fixture.primary.description == fixture.description
    assert fixture.primary.reason == fixture.reason


@pytest.mark.parametrize("key", RULE_KEYS)
async def test_every_reason_states_the_figures_behind_its_suggestion(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """A reason is non-blank, carries a digit, and carries *this* fixture's digits.

    A digit alone is guaranteed by the closing score clause, so asserting that
    would prove nothing; the figures are what make the sentence a *why* rather
    than a restatement of the title.
    """
    fixture = await _build(key, seed, risks, owner, service)

    assert fixture.reason.strip()
    assert any(character.isdigit() for character in fixture.reason)
    assert fixture.reason != fixture.title
    for figure in fixture.figures:
        assert figure in fixture.reason, f"{key} dropped the figure {figure!r}"


@pytest.mark.parametrize("key", RULE_KEYS)
async def test_every_recommendation_links_back_to_its_risk_and_its_entity(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """RELATED DATA is the entity columns and the ``risk_id``, not just the prose.

    A suggestion that names a task in its sentence but carries no ``entity_id``
    is a suggestion the UI cannot link to, so the columns are asserted rather
    than inferred from the copy.
    """
    fixture = await _build(key, seed, risks, owner, service)

    assert fixture.primary.risk_id == fixture.risks[0].id
    assert fixture.primary.entity_type == fixture.entity_type
    assert fixture.primary.entity_id == fixture.entity_id


# ---------------------------------------------------------------------------
# The eight rules, and the boundaries and the one documented gap around them
# ---------------------------------------------------------------------------


async def test_a_blocked_task_is_raised_from_the_tasks_live_status(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """The task branch of the blocked rule, which reads the row rather than the risk.

    A risk's ``metadata`` is a snapshot taken at detection time; the rule reads
    ``tasks.status`` because "it is blocked" has to mean blocked *now*. The
    metadata here still claims the task is blocked and the stored row is not, so
    a rule that trusted the snapshot would raise a suggestion the database
    contradicts.
    """
    project = await seed.project(name="Atlas migration")
    task = await seed.task(project_id=project.id, title="Atlas migration", status=TaskStatus.TODO)
    risk = await _store(
        risks,
        owner,
        risk_type=RiskType.TASK,
        score=70,
        entity_type=ENTITY_TASK,
        entity_id=task.id,
        metadata={"status": TaskStatus.BLOCKED.value},
    )

    assert await service.generate(owner=owner, risks=[risk]) == []


async def test_a_blocked_task_reason_counts_what_is_waiting_on_it(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """Unblock this is a different amount of work with two cards parked behind it.

    The dependent count is related data the risk itself does not carry, so the
    rule reads it from the dependency graph. Two edges are written and the
    sentence names both of them.
    """
    project = await seed.project(name="Atlas migration")
    task = await seed.task(
        project_id=project.id,
        title="Atlas migration",
        status=TaskStatus.BLOCKED,
        estimated_minutes=240,
        updated_at=at(DAY),
    )
    waiting = [await seed.task(project_id=project.id, title=f"Waiting {n}") for n in (1, 2)]
    db_session.add_all(TaskDependency(task_id=row.id, depends_on_id=task.id) for row in waiting)
    await db_session.commit()
    risk = await _store(
        risks,
        owner,
        risk_type=RiskType.TASK,
        severity=RiskSeverity.HIGH,
        score=60,
        entity_type=ENTITY_TASK,
        entity_id=task.id,
        metadata={},
    )

    rows = await service.generate(owner=owner, risks=[risk])

    row = _only(rows, RecommendationType.COMPLETE_BLOCKED_TASK)
    assert row.title == "Resolve the blocked work in Atlas migration"
    assert row.description == (
        "Unblock or re-scope Atlas migration, which is recorded in the blocked "
        "status. 2 task(s) are waiting on it."
    )
    assert row.reason == (
        "Atlas migration is recorded as blocked against an estimate of 4h, last "
        "updated on 05 Jan 2026. 2 task(s) are waiting on it and cannot move while "
        "it is blocked. Recorded risk score 60 of 100 (high severity, low evidence "
        "strength)."
    )


async def test_a_passed_deadline_belongs_to_the_review_rule_and_not_the_block_rule(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """A date that has passed cannot be scheduled against.

    This is the boundary between the two deadline rules and it earns its own case:
    rule 1 declines on a non-positive ``deadline_in_hours`` and rule 2 takes the
    risk, so the risk yields exactly one suggestion and it is the honest one.
    """
    project = await seed.project(name="Ledger cutover")
    task = await seed.task(project_id=project.id, title="Ledger cutover")
    risk = await _store(
        risks,
        owner,
        risk_type=RiskType.DEADLINE,
        severity=RiskSeverity.CRITICAL,
        score=100,
        evidence_strength=EvidenceStrength.MEDIUM,
        entity_type=ENTITY_TASK,
        entity_id=task.id,
        metadata={
            "title": "Ledger cutover",
            "remaining_minutes": 90,
            "available_minutes": 30,
            "deadline_in_hours": -36.0,
        },
    )

    rows = await service.generate(owner=owner, risks=[risk])

    assert _types(rows) == (RecommendationType.REVIEW_DEADLINE,)
    row = rows[0]
    assert row.title == "Check whether Ledger cutover is still achievable"
    assert row.description == (
        "Decide the next step for Ledger cutover: move its due date, reduce its scope, "
        "or record the work as done."
    )
    assert row.reason == (
        "The due date for Ledger cutover passed 36 hours ago with 1h 30m of estimated "
        "work outstanding, of which 30m was booked. Recorded risk score 100 of 100 "
        "(critical severity, medium evidence strength)."
    )


async def test_a_deadline_that_is_far_away_and_fully_booked_raises_nothing(
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """The medium floor on the review rule is what keeps this out of the feed.

    A task due in three weeks with all of its work booked is a healthy plan, and
    a suggestion about it would be noise. The score sits below the floor, so the
    rule declines — and the assertion is that nothing at all is raised rather than
    that a lower-priority row appears.
    """
    risk = await _store(
        risks,
        owner,
        risk_type=RiskType.DEADLINE,
        severity=RiskSeverity.LOW,
        score=10,
        entity_type=ENTITY_TASK,
        entity_id=uuid.uuid4(),
        metadata={
            "title": "Ledger cutover",
            "remaining_minutes": 120,
            "available_minutes": 120,
            "deadline_in_hours": 504.0,
        },
    )

    assert await service.generate(owner=owner, risks=[risk]) == []


async def test_a_workload_that_fits_raises_nothing(
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """The rule's own gate: scheduled minutes under availability is not overload."""
    risk = await _store(
        risks,
        owner,
        risk_type=RiskType.WORKLOAD,
        severity=RiskSeverity.LOW,
        score=10,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        metadata={"scheduled_minutes": 900, "available_minutes": 1800},
    )

    assert await service.generate(owner=owner, risks=[risk]) == []


async def test_a_scheduling_risk_raises_no_suggestion(
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """The documented gap, pinned so that adding a ninth rule is a visible change.

    The contracts freeze eight rules and none of them proposes an action for a
    fault in the plan, so ``RiskType.SCHEDULING`` maps to no rules at all. That
    is a deliberate reading rather than an oversight, and the cost of it is that a
    scheduling risk sits in the Risk Center without a suggested action. Asserting
    the gap means the day someone adds the rule, this test fails and says why.
    """
    assert recommendation_rules[RiskType.SCHEDULING] == ()

    risk = await _store(
        risks,
        owner,
        risk_type=RiskType.SCHEDULING,
        severity=RiskSeverity.HIGH,
        score=64,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        metadata={
            "overlapping_sessions": 3,
            "outside_availability_sessions": 2,
            "sessions_after_deadline": 1,
            "longest_consecutive_run": 5,
        },
    )

    assert await service.generate(owner=owner, risks=[risk]) == []
    assert await _all_rows(db_session, owner) == []


async def test_a_risk_the_user_has_already_closed_raises_no_suggestion(
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """Only live risks speak. Re-proposing for a dismissed condition is talking over them."""
    risk = await _store(
        risks,
        owner,
        risk_type=RiskType.WORKLOAD,
        severity=RiskSeverity.HIGH,
        score=53,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        metadata={"scheduled_minutes": 2280, "available_minutes": 1800},
    )
    await risks.transition_risk(
        owner.id, risk.id, status=RiskStatus.DISMISSED.value, responded=True
    )

    assert await service.generate(owner=owner, risks=[risk]) == []


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


async def test_generating_twice_over_unchanged_risks_creates_nothing_the_second_time(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """Hundreds of identical records is a cross-run failure, not a cross-rule one.

    The first pass raises one blocked-work and one review-the-signals suggestion
    from the project risk; the second must add nothing at all, because the open
    suggestions are already saying exactly what the rules say. The count is read
    back from storage rather than from the second call's return value alone — a
    return value of ``[]`` with a row written anyway would pass the first half of
    this assertion and fail the second.
    """
    first = await _build("complete_blocked_task", seed, risks, owner, service)
    assert len(first.rows) == 2

    second = await service.generate(owner=owner, risks=list(first.risks))

    assert second == []
    stored = await _all_rows(db_session, owner)
    assert [row.recommendation_type for row in stored] == [
        RecommendationType.COMPLETE_BLOCKED_TASK.value,
        RecommendationType.REVIEW_PROJECT.value,
    ]


async def test_a_suggestion_whose_numbers_moved_is_refreshed_not_duplicated(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """The reason *is* the numbers, so a moved number is a moved reason.

    A refresh that kept the first run's wording would leave the suggestion
    arguing for something the data no longer says — "schedule another 3h" when the
    gap is now six. The row count staying at one and the ``created_at`` staying
    put are both asserted: the first because a duplicate is the failure, the
    second because the repository documents that a refreshed recommendation keeps
    its creation instant, and "since when has this been suggested" is worth more
    to the UI than a reordering.
    """
    first = await _build("block_time", seed, risks, owner, service)
    before = await _stored(db_session, first.primary.id)
    assert "leaving 3h with no time scheduled" in before.reason

    refreshed = await _store(
        risks,
        owner,
        risk_type=RiskType.DEADLINE,
        severity=RiskSeverity.HIGH,
        score=60,
        evidence_strength=EvidenceStrength.MEDIUM,
        entity_type=ENTITY_TASK,
        entity_id=first.risks[0].entity_id,
        metadata={
            "title": "Atlas data migration",
            "remaining_minutes": 480,
            "available_minutes": 120,
            "deadline_in_hours": 24.0,
        },
    )
    assert refreshed.id == first.risks[0].id, "the risk itself should have been updated"

    second = await service.generate(owner=owner, risks=[refreshed])

    assert second == [], "a refreshed suggestion is not a creation"
    stored = await _all_rows(db_session, owner)
    assert len(stored) == 1
    assert stored[0].id == before.id
    assert stored[0].created_at == before.created_at
    assert "leaving 6h with no time scheduled" in stored[0].reason
    assert "leaving 3h with no time scheduled" not in stored[0].reason
    assert "Schedule another 6h" in stored[0].title


async def test_a_rejected_recommendation_is_raised_again(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """The open-status index is over ``new``/``viewed``, so a rejection frees the row.

    The failure this guards against is silent and one-sided: if the index were
    over "not terminal" instead, the rejected row would keep blocking a legitimate
    re-raise for as long as the condition stood, and a user who fixed something
    and then saw it again would be right to be annoyed. Two rows must therefore
    exist after the second pass, with the first still carrying its rejection.
    """
    first = await _build("reduce_workload", seed, risks, owner, service)
    rejected = await service.reject(owner=owner, recommendation_id=first.primary.id)
    assert rejected.status == RecommendationStatus.REJECTED.value

    second = await service.generate(owner=owner, risks=list(first.risks))

    assert len(second) == 1
    assert second[0].id != rejected.id
    stored = await _all_rows(db_session, owner)
    assert [row.status for row in stored] == [
        RecommendationStatus.REJECTED.value,
        RecommendationStatus.NEW.value,
    ]


async def test_a_viewed_recommendation_is_not_raised_again(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """Reading a card twice is a normal thing to do; re-raising it is nagging.

    The other side of the open-status index. ``viewed`` is inside the open set,
    so a suggestion the user has merely looked at is not written again — which is
    the same decision that keeps ``view`` from stamping ``responded_at``.
    """
    first = await _build("reduce_workload", seed, risks, owner, service)
    await service.view(owner=owner, recommendation_id=first.primary.id)

    second = await service.generate(owner=owner, risks=list(first.risks))

    assert second == []
    stored = await _all_rows(db_session, owner)
    assert [row.status for row in stored] == [RecommendationStatus.VIEWED.value]


async def test_a_condition_handed_to_generate_twice_is_written_once(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """The in-pass ``seen`` set, exercised the only way the identity model allows.

    A rule pair agreeing on one identity is the case ``generate``'s per-pass set
    exists for, and it is not reachable through two *distinct* risks: the risk
    table's own partial index over ``(user_id, risk_type, entity_type,
    entity_id)`` collapses those into one row before the recommendation engine
    ever sees them, so a second live project risk for the same project cannot
    exist. The reachable trigger is therefore a caller passing the same risk
    twice, which ``generate`` accepts and must absorb without writing or
    reporting a second suggestion.
    """
    fixture = await _build("complete_blocked_task", seed, risks, owner, service)
    assert len(fixture.rows) == 2

    rows = await service.generate(owner=owner, risks=[fixture.risks[0], fixture.risks[0]])

    assert rows == []
    stored = await _all_rows(db_session, owner)
    assert [row.id for row in stored] == [row.id for row in fixture.rows]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("move", ANSWERING_MOVES)
async def test_an_answering_move_stamps_responded_at_and_records_its_event(
    move: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """Accept, reject and complete are three answers, and each records itself.

    "I will do this" and "I did this" are different facts about different
    moments, so each move is asserted separately and its own event is looked for.

    ``from_status`` is deliberately *not* asserted here. The service reads the
    current status before the write in order to record where the transition came
    from, but on a request-scoped session both reads resolve to the same
    identity-mapped object, so the value written is the status the row was just
    moved to. That is reported as a defect rather than pinned here; asserting it
    either way would put a test on the wrong side of a fix that has not been made.
    """
    fixture = await _build("reduce_workload", seed, risks, owner, service)
    status, event = ANSWERING_OUTCOMES[move]

    row = await getattr(service, move)(owner=owner, recommendation_id=fixture.primary.id)

    assert row.status == status.value
    assert row.responded_at is not None
    stored = await _stored(db_session, row.id)
    assert stored.status == row.status
    assert stored.responded_at is not None
    written = await _events(db_session, owner, event)
    assert len(written) == 1
    assert written[0].metadata_["recommendation_id"] == str(row.id)
    assert written[0].metadata_["status"] == row.status
    unchosen = [name for name, (target, _) in ANSWERING_OUTCOMES.items() if target is not status]
    for name in unchosen:
        stray_event = ANSWERING_OUTCOMES[name][1]
        assert await _events(db_session, owner, stray_event) == [], (
            f"{move} also wrote the {name} event"
        )


async def test_viewing_a_recommendation_stamps_nothing_and_is_idempotent(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """Opening a card is not answering it, and reading it twice is not an error.

    A system that counted a read as a response would train itself on a signal
    that says nothing, so ``responded_at`` must still be null afterwards. The
    second call matters too: re-opening a card is normal, and a transition that
    raised on it would be a bug in the feed rather than in the data.
    """
    fixture = await _build("reduce_workload", seed, risks, owner, service)

    first = await service.view(owner=owner, recommendation_id=fixture.primary.id)
    second = await service.view(owner=owner, recommendation_id=fixture.primary.id)

    assert first.status == RecommendationStatus.VIEWED.value
    assert second.status == RecommendationStatus.VIEWED.value
    stored = await _stored(db_session, first.id)
    assert stored.responded_at is None
    viewed = await _events(db_session, owner, ActivityEvent.RECOMMENDATION_VIEWED)
    assert len(viewed) == 2, "each view is recorded; neither of them is an answer"


async def test_a_completed_recommendation_can_follow_an_accepted_one(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """Accept-then-complete is the sequence accepting exists to make expressible.

    Completing a suggestion nobody accepted is also legitimate — they may simply
    have done the thing — and the two are distinguishable afterwards. This
    asserts the ordered pair works, rather than only that each move works on a
    ``new`` row.
    """
    fixture = await _build("reduce_workload", seed, risks, owner, service)

    accepted = await service.accept(owner=owner, recommendation_id=fixture.primary.id)
    completed = await service.complete(owner=owner, recommendation_id=accepted.id)

    assert completed.status == RecommendationStatus.COMPLETED.value
    assert completed.responded_at >= accepted.responded_at


async def test_a_terminal_recommendation_refuses_a_further_transition_naming_its_status(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """The conflict has to say which state the row is in, or the message is useless.

    A rejection is terminal: the user has answered, and re-opening the same row
    would overwrite the label that makes it worth having. The message names the
    state and the move in words the reader already uses — "was declined", "marked
    as accepted" — because this string is what the API surfaces to the person
    who pressed the button, and the bare enum members told them two machine words
    and no sentence. The machine-readable pair stays on ``details``.
    """
    fixture = await _build("reduce_workload", seed, risks, owner, service)
    await service.reject(owner=owner, recommendation_id=fixture.primary.id)

    with pytest.raises(ConflictError) as raised:
        await service.accept(owner=owner, recommendation_id=fixture.primary.id)

    assert (
        str(raised.value) == "This recommendation was declined, so it cannot be marked as accepted."
    )
    assert raised.value.details["status"] == RecommendationStatus.REJECTED.value
    assert raised.value.details["target"] == RecommendationStatus.ACCEPTED.value


async def test_a_completed_recommendation_cannot_be_viewed_afterwards(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """There is no answer to read once the answer has been given.

    The mirror of the rejection case, and the one a UI can hit by accident: a
    stale page still showing a card the user has completed, opened a second time.
    """
    fixture = await _build("reduce_workload", seed, risks, owner, service)
    await service.complete(owner=owner, recommendation_id=fixture.primary.id)

    with pytest.raises(ConflictError) as raised:
        await service.view(owner=owner, recommendation_id=fixture.primary.id)

    assert str(raised.value) == "This recommendation was completed, so it cannot be marked as read."


async def test_transitioning_a_recommendation_that_does_not_exist_is_not_found(
    service: RecommendationService,
    owner: User,
) -> None:
    """An unknown id and a foreign one are the same answer, so neither probes."""
    with pytest.raises(
        NotFoundError,
        match="That recommendation does not exist",
    ):
        await service.accept(owner=owner, recommendation_id=uuid.uuid4())


async def test_another_accounts_recommendation_is_not_found_not_forbidden(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """A forbidden answer would confirm the id exists, so the two cases are identical.

    The same rule Phase 6 settled for owned projects, restated here because the
    lifecycle routes are where a guessed id is most tempting to probe with.
    """
    fixture = await _build("reduce_workload", seed, risks, owner, service)
    stranger = await register_user(db_session, username="bob", email="bob@nexus.test")

    with pytest.raises(
        NotFoundError,
        match="That recommendation does not exist",
    ):
        await service.accept(owner=stranger, recommendation_id=fixture.primary.id)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


async def test_expire_for_resolved_closes_the_suggestions_whose_risk_went_away(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """A suggestion attached to a risk that no longer exists is *moot*, not old.

    Recording that is more useful than deleting the row: a suggestion the user
    never saw because its problem went away is a different observation from one
    they declined, and collapsing the two would teach a future model that silence
    meant "no". The returned count is asserted because the caller writes it into
    the run summary.
    """
    closed = await _build("reduce_workload", seed, risks, owner, service)
    still_live = await _build("review_deadline", seed, risks, owner, service)
    await risks.transition_risk(
        owner.id, closed.risks[0].id, status=RiskStatus.RESOLVED.value, responded=True
    )

    expired = await service.expire_for_resolved(owner=owner, risk_ids=[closed.risks[0].id])

    assert expired == 1
    row = await _stored(db_session, closed.primary.id)
    assert row.status == RecommendationStatus.EXPIRED.value
    assert row.expires_at is not None
    assert row.responded_at is None, "expiry is not something the user did"
    other = await _stored(db_session, still_live.primary.id)
    assert other.status == RecommendationStatus.NEW.value
    assert other.expires_at is None


async def test_expire_for_resolved_leaves_an_answered_suggestion_alone(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """A rejection is the user's label and expiry is the engine's; they do not merge.

    The repository's open-status predicate is what enforces this, so the test
    drives it from the service: a count of zero and a row still reading
    ``rejected`` is the outcome that keeps the training signal clean.
    """
    fixture = await _build("reduce_workload", seed, risks, owner, service)
    await service.reject(owner=owner, recommendation_id=fixture.primary.id)
    await risks.transition_risk(
        owner.id, fixture.risks[0].id, status=RiskStatus.RESOLVED.value, responded=True
    )

    expired = await service.expire_for_resolved(owner=owner, risk_ids=[fixture.risks[0].id])

    assert expired == 0
    stored = await _stored(db_session, fixture.primary.id)
    assert stored.status == RecommendationStatus.REJECTED.value
    assert stored.expires_at is None


async def test_expire_for_resolved_is_a_no_op_for_no_risks_and_for_a_live_one(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """Nothing to close, and nothing to close *yet*.

    A risk that is still live is the case that matters: expiry is a claim about
    the risk being gone, and the statement re-checks that rather than trusting
    the caller's list.
    """
    fixture = await _build("reduce_workload", seed, risks, owner, service)

    assert await service.expire_for_resolved(owner=owner, risk_ids=[]) == 0
    assert await service.expire_for_resolved(owner=owner, risk_ids=[fixture.risks[0].id]) == 0
    stored = await _stored(db_session, fixture.primary.id)
    assert stored.status == RecommendationStatus.NEW.value


# ---------------------------------------------------------------------------
# Priority is derived, never chosen
# ---------------------------------------------------------------------------


async def test_priority_is_the_raising_risk_severity_in_every_band(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """One number in two renderings — across the whole ladder, not one band of it.

    Four risks identical except for their severity, on four different tasks so
    none of them collides with another through the deduplicating index. A rule
    that could pick its own priority would have four chances here to disagree with
    the band it was handed, and the assertion is that none of them did.
    """
    raised: list[Recommendation] = []
    expected: dict[uuid.UUID | None, str] = {}
    for index, severity in enumerate(RiskSeverity):
        project = await seed.project(name=f"Project {index}")
        task = await seed.task(project_id=project.id, title=f"Task {index}")
        risk = await _store(
            risks,
            owner,
            risk_type=RiskType.DEADLINE,
            severity=severity,
            score=60,
            entity_type=ENTITY_TASK,
            entity_id=task.id,
            metadata={
                "title": f"Task {index}",
                "remaining_minutes": 300,
                "available_minutes": 120,
                "deadline_in_hours": 24.0,
            },
        )
        rows = await service.generate(owner=owner, risks=[risk])
        assert len(rows) == 1
        raised.append(rows[0])
        expected[task.id] = severity.value

    assert {row.entity_id: row.priority for row in raised} == expected
    stored = await _all_rows(db_session, owner)
    assert sorted(row.priority for row in stored) == sorted(expected.values())


def test_no_rule_has_a_way_to_choose_its_own_priority() -> None:
    """The property is structural: there is no parameter to pass.

    Four severities is evidence; the signature is the proof. Every rule method
    takes keyword-only ``owner`` and ``risk`` and nothing else, so "priority
    comes from severity" cannot be true of one rule and false of another — a
    rule that wanted to say "this one is urgent" would have to raise a risk that
    says so, which is the only place urgency is a fact about anything.

    **Ten rules, not eight.** Phase 9 adds two under the ``None`` key — the
    learning rules, which are raised from the user's own record and have no risk
    to derive anything from — and they are held to the *same* signature: keyword
    only, ``owner`` and ``risk`` and nothing else, and still no ``priority``. A
    learning rule that could pass its own band would break the guarantee the other
    eight keep, and the way it would break it is exactly the way the first eight
    could.
    """
    registered = {name for rule_names in recommendation_rules.values() for name in rule_names}
    assert len(registered) == 10, "eight risk-raised rules plus the two Phase 9 learning rules"
    for scope, rule_names in recommendation_rules.items():
        label = scope.value if scope is not None else "learning"
        for name in rule_names:
            signature = inspect.signature(getattr(RecommendationService, name))
            assert "priority" not in signature.parameters, f"{label}/{name} can set a priority"
            # ``self`` apart, every rule is handed exactly the two arguments
            # ``generate`` and ``generate_learning`` pass and nothing else, and
            # both keyword-only, so no rule can widen what it is given either.
            assert set(signature.parameters) - {"self"} == {"owner", "risk"}, (
                f"{name} has an unexpected parameter"
            )
            for parameter in signature.parameters.values():
                if parameter.name == "self":
                    continue
                assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
                    f"{label}/{name}/{parameter.name} is positional"
                )


def test_the_learning_rules_are_registered_under_the_key_that_means_no_risk() -> None:
    """One registry, one dispatch — and the Phase 9 rules are in it.

    The contracts extend this engine rather than building a parallel path, so the
    thing worth pinning is that the two new rules are *names in the same table*
    that :meth:`RecommendationService._rule` resolves, and that they are keyed
    apart from every :class:`~app.models.enums.RiskType`: a risk type is a
    condition a detector can re-derive, and neither a goal's deadline nor a
    dormant skill is one.

    Both halves are asserted rather than described: the key is literally ``None``,
    and both names resolve to methods on the class.
    """
    assert recommendation_rules[None] == (
        "_rule_review_learning_goal",
        "_rule_revive_target_skill",
    )
    assert None not in set(RiskType)
    for name in recommendation_rules[None]:
        assert callable(getattr(RecommendationService, name))


# Cold start
# ---------------------------------------------------------------------------


async def test_an_account_with_no_risks_gets_no_suggestions_and_no_history(
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """No risks in, nothing out — and nothing invented to fill the gap.

    "Not enough data yet" is the brief's stated answer for a new account, and the
    specific way it fails is a fabricated zero: a recommendation table with a row
    in it saying nothing, which reads as "the engine looked and found this". So
    the assertions cover the return value, the stored rows, the event log *and*
    the run history, because a run summary written for a pass that judged nothing
    would be history nobody can interpret.
    """
    rows = await service.generate(owner=owner, risks=[])

    assert rows == []
    assert await _all_rows(db_session, owner) == []
    stored, total = await risks.list_recommendations(owner.id, limit=50, offset=0)
    assert stored == []
    assert total == 0
    assert await risks.list_evaluations(owner.id, limit=20) == []
    created = await _events(db_session, owner, ActivityEvent.RECOMMENDATION_CREATED)
    assert created == []


# ---------------------------------------------------------------------------
# Regression guards
# ---------------------------------------------------------------------------

#: The three detectors that raise a risk about the account rather than about a
#: row. All three produce a null ``entity_id``, and a null identity is exactly the
#: case the repository cannot arbitrate with its partial index — so all three take
#: the ORM path, which is where the metadata regression lived.
ACCOUNT_RULE_KEYS = ("reduce_workload", "update_estimate", "review_consistency")


async def test_the_twenty_one_character_recommendation_type_round_trips(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """``complete_blocked_task`` is 21 characters and the column was once 16.

    The blocked-task rule fired correctly and then died in the database, because
    the column truncated the single longest value in the vocabulary on insert. A
    fixture that only asserted the row existed would have caught the *absence* and
    not the *corruption*, so the string is read back out of storage and compared
    against the enum member rather than against the rule's intent.
    """
    fixture = await _build("complete_blocked_task", seed, risks, owner, service)
    row = _only(fixture.rows, RecommendationType.COMPLETE_BLOCKED_TASK)
    assert len(RecommendationType.COMPLETE_BLOCKED_TASK.value) == 21

    stored = await _stored(db_session, row.id)

    assert stored.recommendation_type == "complete_blocked_task"
    assert stored.recommendation_type == RecommendationType.COMPLETE_BLOCKED_TASK.value


@pytest.mark.parametrize("key", ACCOUNT_RULE_KEYS)
async def test_metadata_survives_the_account_level_upsert_path(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """An account-level suggestion keeps its metadata; the row-level ones always did.

    The second half of the same defect as the truncated column, and the quieter
    one. ``metadata`` is reserved on a declarative class, so the column is
    declared under the attribute ``metadata_``; writing the ORM instance with the
    *column* name set a stray instance attribute that SQLAlchemy accepted and
    never persisted. Every account-level risk — workload, consistency and
    estimation, three of the six detectors — therefore stored ``{}`` while its
    sibling going through ``INSERT ... ON CONFLICT`` stored correctly, and the
    symptom was invisible until something read the column back.

    The assertion is exact equality on the whole document, through
    :func:`_stored`, for each of the three rules that take this path.
    """
    fixture = await _build(key, seed, risks, owner, service)
    assert fixture.primary.entity_id is None, "this path is only for null identities"

    stored = await _stored(db_session, fixture.primary.id)

    assert stored.metadata_ == {
        "rule": fixture.primary.recommendation_type,
        "risk_type": fixture.risks[0].risk_type,
        "risk_score": int(fixture.risks[0].score),
        "risk_severity": fixture.risks[0].severity,
        "evidence_strength": fixture.risks[0].evidence_strength,
    }


async def test_metadata_survives_a_refresh_on_the_account_level_path(
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """The refresh path writes through the same ORM attributes, and is asserted too.

    The create path and the update path are two different statements in
    :meth:`~app.repositories.risk.RiskRepository._upsert_null_identity_recommendation`,
    and either one can carry the metadata across on its own. The numbers are moved
    so that the write has something to write, and the document is compared
    afterwards rather than merely checked for being non-empty.
    """
    first = await _build("reduce_workload", seed, risks, owner, service)
    refreshed = await _store(
        risks,
        owner,
        risk_type=RiskType.WORKLOAD,
        severity=RiskSeverity.CRITICAL,
        score=77,
        evidence_strength=EvidenceStrength.HIGH,
        entity_type=ENTITY_ACCOUNT,
        entity_id=None,
        metadata={
            "scheduled_minutes": 3000,
            "available_minutes": 1800,
            "window_label": WINDOW_LABEL,
        },
    )
    assert refreshed.id == first.risks[0].id

    assert await service.generate(owner=owner, risks=[refreshed]) == []

    stored = await _all_rows(db_session, owner)
    assert len(stored) == 1
    assert stored[0].priority == RecommendationPriority.CRITICAL.value
    assert stored[0].metadata_["risk_score"] == 77
    assert stored[0].metadata_["risk_severity"] == RiskSeverity.CRITICAL.value
    assert stored[0].metadata_["evidence_strength"] == EvidenceStrength.HIGH.value
    assert first.primary.id == stored[0].id


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", RULE_KEYS)
async def test_no_recommendation_uses_a_banned_register(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """Every sentence a user can be shown is scanned, not just the title.

    The brief rules out a vocabulary of judgement — "you are failing", "falling
    behind badly" — and the ban is on the *register*, not on the rule's logic. A
    scan over the whole suggestion catches a judgement introduced in any of the
    three fields, which a test on the title alone would not: the sentence a
    reader actually reads is usually the description.
    """
    fixture = await _build(key, seed, risks, owner, service)

    for field, text in (
        ("title", fixture.primary.title),
        ("description", fixture.primary.description),
        ("reason", fixture.primary.reason),
    ):
        found = _prohibitions(text)
        assert not found, f"the {key} {field} uses a banned register: {found}"


@pytest.mark.parametrize("key", RULE_KEYS)
async def test_the_headline_and_the_reason_avoid_the_banned_words_entirely(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """The stricter list, applied to the two fields where there is nothing to narrow.

    ``title`` and ``reason`` contain no use of "behind" in any rule, so on these
    two fields the word-level check is exact rather than interpreted. It is the
    compromise that lets :data:`NEUTRALITY_PROHIBITIONS` narrow the bare word in
    the descriptions without leaving a hole in the fields a reader scans first.
    """
    fixture = await _build(key, seed, risks, owner, service)

    for field, text in (("title", fixture.primary.title), ("reason", fixture.primary.reason)):
        found = _prohibitions(text, HEADLINE_PROHIBITIONS)
        assert not found, f"the {key} {field} uses a banned word: {found}"


@pytest.mark.parametrize("key", RULE_KEYS)
async def test_no_suggestion_tells_the_user_the_engine_has_already_acted(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
) -> None:
    """Every type names something a person does, and the copy says so too.

    The brief is explicit that nothing is rescheduled without confirmation, and
    that is enforced structurally by the type vocabulary — no member means "the
    system did it". The wording is where it could be undone, though: a
    description in the past tense would assert an action the engine has no way to
    take. Both registers of that failure are listed, because a future tense is
    just as wrong as a past one.
    """
    fixture = await _build(key, seed, risks, owner, service)

    lowered = fixture.primary.description.lower()
    for phrase in ("has been rescheduled", "was rescheduled", "has been moved", "we rescheduled"):
        assert phrase not in lowered, f"the {key} description implies an action was taken"


@pytest.mark.parametrize("key", RULE_KEYS)
async def test_every_suggestion_is_written_for_one_account_only(
    key: str,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """Ownership is a predicate in the write, and the row carries the caller's id.

    Small, but it is the check that a recommendation raised from a risk read
    through somebody else's query cannot be attributed to the reader who is
    looking at it.
    """
    fixture = await _build(key, seed, risks, owner, service)

    stored = await _stored(db_session, fixture.primary.id)

    assert stored.user_id == owner.id


#: How far out the task's due date sits, in the three regression cases below.
#: Today, tomorrow and three days out are the three a calendar word can get
#: wrong, and they fail in the same direction: a day read late is still a
#: perfectly well-formed date, so only the day's own number tells them apart.
DUE_HORIZONS = (0, 1, 3)


@pytest.mark.parametrize("days_out", DUE_HORIZONS)
async def test_the_due_date_named_on_the_first_pass_is_the_tasks_own(
    days_out: int,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """A deadline sentence names the task's own due date, on the very first pass.

    **The defect this guards.** The due phrase used to be rebuilt as
    ``detected_at + deadline_in_hours``: a gap the detector measures at the
    *start* of its pass, added to the instant the row is *written*. Two clocks in
    one sum, and the answer is ``due_date + (written_at - measured_at)``. On an
    ordinary pass the two agree to the second and the copy is right; on a pass
    that begins before midnight and writes its rows after it the sum lands a whole
    day late. The next pass repairs the text, which is exactly why the defect
    survives — the bad day is what the user is shown first, and a reason is
    persisted, so it is stored too.

    **The fixture.** ``deadline_in_hours`` is computed the way
    :func:`~app.services.risk.detection._hours_until` computes it — to the end of
    the due day, from the database clock — and ``detected_at`` is then moved one
    day later, which is what a midnight-straddling pass produces and what turns a
    latent disagreement into a visible one. The old sum reads *the day after the
    due date* in all three cases, so this test fails on every parameter rather
    than only when the suite happens to run at 23:59.

    **The expected sentence**, written from the fixture's own numbers: 300
    estimated minutes with 120 booked leaves a 180-minute gap, so the title says
    "3h" and the reason says "5h of estimated work remains", "2h is booked" and
    "3h with no time scheduled" beside the detector's own score of 60 of 100
    (high severity, medium evidence strength). The date in all three fields is the
    task's ``due_date`` rendered ``%d %b %Y``.
    """
    now = await _db_now(db_session)
    due = now.date() + timedelta(days=days_out)
    # The detector's own convention: ``tasks.due_date`` is a date, so it decides
    # on the end of that day. Quoted rather than approximated so the fixture is a
    # deadline risk the pass could really have written.
    hours = (datetime.combine(due, time.max, tzinfo=UTC) - now).total_seconds() / 3600.0
    project = await seed.project(name="Atlas data migration")
    task = await seed.task(project_id=project.id, title="Atlas data migration", due_date=due)
    risk = await _store(
        risks,
        owner,
        risk_type=RiskType.DEADLINE,
        severity=RiskSeverity.HIGH,
        score=60,
        evidence_strength=EvidenceStrength.MEDIUM,
        entity_type=ENTITY_TASK,
        entity_id=task.id,
        metadata={
            "title": "Atlas data migration",
            "remaining_minutes": 300,
            "available_minutes": 120,
            "deadline_in_hours": hours,
        },
    )
    await db_session.execute(
        update(Risk)
        .where(Risk.id == risk.id)
        .values(detected_at=now + timedelta(days=1))
        .execution_options(synchronize_session=False)
    )
    await db_session.commit()
    stamped = (
        await db_session.execute(
            select(Risk).where(Risk.id == risk.id).execution_options(populate_existing=True)
        )
    ).scalar_one()

    rows = await service.generate(owner=owner, risks=[stamped])

    row = _only(rows, RecommendationType.BLOCK_TIME)
    assert row.title == f"Schedule another 3h for Atlas data migration before {due:%d %b %Y}"
    assert row.description == (
        "Add 3h of unscheduled work to Atlas data migration on or before "
        f"{due:%d %b %Y}, so the time exists before the date it is needed."
    )
    assert row.reason == (
        "5h of estimated work remains on Atlas data migration and 2h is booked "
        f"before {due:%d %b %Y}, leaving 3h with no time scheduled. Recorded risk "
        "score 60 of 100 (high severity, medium evidence strength)."
    )
    stored = await _stored(db_session, row.id)
    assert stored.reason == row.reason, "a wrong day here would be persisted, not merely shown"


@pytest.mark.parametrize("status", [RiskStatus.RESOLVED, RiskStatus.DISMISSED])
async def test_closing_a_risk_expires_the_suggestions_attached_to_it(
    status: RiskStatus,
    seed: AnalyticsSeed,
    risks: RiskRepository,
    owner: User,
    service: RecommendationService,
    db_session: AsyncSession,
) -> None:
    """A risk the user closes takes its open suggestions with it, either way.

    Both terminal statuses are covered because both are reachable from the Risk
    Center — ``POST /{risk_id}/resolve`` and ``POST /{risk_id}/dismiss`` — and a
    hook that worked for one and not the other would leave half the closable risks
    holding suggestions for a condition its owner has already answered.

    The expected figures are the two columns themselves: the closed risk's
    suggestion goes to ``expired`` with ``expires_at`` stamped, the suggestion on
    a risk that is still live stays ``new`` with ``expires_at`` null, and
    ``responded_at`` stays null on both, because the user answered the *risk* and
    never opened or declined the suggestion. That last one is the training label:
    a suggestion that went moot is a different observation from one they refused.

    This drives the two steps a close is made of — the repository transition, then
    :meth:`RecommendationService.expire_for_resolved` — which is what
    ``_transition`` in ``app/api/v1/risks.py`` has to do and, as of this writing,
    does not: that helper calls ``RiskRepository.transition_risk`` and nothing
    else, so a suggestion closed from the Risk Center stays ``new`` forever. The
    seam is pinned here; the wiring is a defect reported against a file this
    change does not own.
    """
    closed = await _build("block_time", seed, risks, owner, service)
    still_live = await _build("reduce_workload", seed, risks, owner, service)

    await risks.transition_risk(owner.id, closed.risks[0].id, status=status.value, responded=True)
    expired = await service.expire_for_resolved(owner=owner, risk_ids=[closed.risks[0].id])

    assert expired == 1
    row = await _stored(db_session, closed.primary.id)
    assert row.status == RecommendationStatus.EXPIRED.value
    assert row.expires_at is not None
    assert row.responded_at is None, "the user closed the risk, not the suggestion"
    other = await _stored(db_session, still_live.primary.id)
    assert other.status == RecommendationStatus.NEW.value
    assert other.expires_at is None


# ---------------------------------------------------------------------------
# Phase 9 — learning recommendations raised with no risk behind them
# ---------------------------------------------------------------------------

#: The two registers Phase 9 bans on top of the shared
#: :data:`NEUTRALITY_PROHIBITIONS`. The first is a claim about somebody's
#: ability, which the phase's first rule rules out and which the level-phrase
#: mechanism exists to prevent; the second quotes a duration as though it measured
#: the person, which is the honest-language rule restated for a phase whose
#: evidence table records *that something was logged* rather than how anyone
#: spent a month. Matched on word boundaries, for the reason the list above is.
_PERSON_PROHIBITIONS = (
    "not good at",
    "weak at",
    "weak",
    "bad at",
    "struggling with",
    "unproductive",
    "inactive person",
    "lazy",
    "hours worked",
    "hours of work",
    "put in",
    "mastery",
    "proficiency",
    "proficient",
    "aptitude",
)


def _learning_service(session: AsyncSession) -> RecommendationService:
    """Wire the service the way ``get_recommendation_service`` does, plus learning.

    Module-level and mirroring the API wiring rather than the ``service`` fixture,
    because the two sweeps need different collaborators: the learning rules read
    ``learning_goals`` and ``skills`` and have no risk to walk, so the fixture that
    exercises the Phase 7 rules cannot stand in for this one. Everything the
    Phase 7 fixture passes is passed here too, so a test that reaches both halves
    gets one service rather than two that happen to agree.

    ``stale_inactive_days`` is left at the module default on purpose — it is
    :data:`DEFAULT_STALE_INACTIVE_DAYS`, the same number
    ``settings.career_stale_inactive_days`` documents, and a test that wanted the
    other one would say so explicitly rather than inheriting a settings object
    nobody read.
    """
    return RecommendationService(
        RiskRepository(session),
        TaskRepository(session),
        ProjectRepository(session),
        ActivityService(ActivityRepository(session)),
        LearningRepository(session),
    )


@pytest.fixture
def learning(db_session: AsyncSession) -> LearningRepository:
    """Phase 9 learning persistence, for writing the goals and skills the rules read."""
    return LearningRepository(db_session)


@pytest.fixture
def learning_service(db_session: AsyncSession) -> RecommendationService:
    """The Phase 9 sweep, wired by :func:`_learning_service`."""
    return _learning_service(db_session)


async def _db_now(session: AsyncSession) -> datetime:
    """The database clock, as an aware UTC instant.

    Read through the database rather than ``datetime.now()`` for the reason
    :mod:`app.services.learning.service` gives at length: both Phase 9 rules are
    arithmetic on a *distance in days*, so a host clock that drifted from the
    server's would seed a fixture whose own dates disagreed with the rule's.

    Normalised to UTC for the same reason the rule normalises. ``func.now()`` is a
    ``timestamptz`` returned **labelled with the connection's** ``TimeZone``, and on
    this server that is ``Asia/Calcutta`` — so the value arrives as the same instant
    wearing the local zone. The arithmetic on the instant is therefore unaffected,
    but every ``.date()`` taken off this helper downstream was the *server-local*
    day while the rules computed against the UTC one, which is a whole day of error
    on a "passed N day(s) ago" figure for the five and a half hours a day the two
    calendars disagree.
    """
    value = await session.scalar(select(func.now()))
    assert isinstance(value, datetime)
    if value.tzinfo is None:  # pragma: no cover - psycopg returns aware values
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _goal_columns(
    session: AsyncSession, goal_id: uuid.UUID
) -> tuple[str, str, int, date | None]:
    """Read back ``(title, status, progress, target_date)`` as plain columns.

    A column tuple rather than the ORM entity, because the identity map still
    holds the object :meth:`LearningRepository.create_goal` returned: an entity
    read would hand back that cached instance, and an assertion about what the
    rule saw would then be comparing a fixture with itself. The values are checked
    here so that the expected sentence below is derived from the row that exists
    rather than from the arguments the fixture hoped for.
    """
    result = await session.execute(
        select(
            LearningGoal.title,
            LearningGoal.status,
            LearningGoal.progress,
            LearningGoal.target_date,
        ).where(LearningGoal.id == goal_id)
    )
    title, status, progress, target_date = result.one()
    return title, status, progress, target_date


async def _skill_columns(
    session: AsyncSession, skill_id: uuid.UUID
) -> tuple[str, int, int, str, int, datetime | None]:
    """Read back a skill's ``(name, current, target, source, count, last)`` columns.

    Column tuple for the same reason as :func:`_goal_columns`, and it matters more
    here: :meth:`LearningRepository.record_skill_evidence` returns the refreshed
    entity, and every figure the dormant-skill rule quotes is one of these
    columns.
    """
    result = await session.execute(
        select(
            Skill.name,
            Skill.current_level,
            Skill.target_level,
            Skill.level_source,
            Skill.evidence_count,
            Skill.last_activity_at,
        ).where(Skill.id == skill_id)
    )
    name, current, target, source, evidence, last = result.one()
    return name, current, target, source, evidence, last


async def test_a_goal_whose_target_date_is_near_is_raised_with_its_own_figures(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """Rule 9: 14 days to the user's own target date, 35% recorded progress.

    The fixture's figures are the contracts' own worked example, so the expected
    sentence is written by hand from them rather than copied from a run: a goal
    titled "Machine Learning", a ``target_date`` exactly 14 days after the
    database clock, and ``progress`` 35 — both columns the *user* filled in and
    neither one NEXUS computed. The row is read back through :func:`_goal_columns`
    first, so the three figures quoted below are the ones storage actually holds.

    Fourteen days past the 7-day ceiling and inside the 30 puts the goal in the
    ``medium`` band of :data:`_GOAL_DEADLINE_PRIORITY`. The reason's closing
    sentence states that the percentage is on the record rather than estimated —
    the honesty sentence the whole phase turns on, and the one a well-meaning
    rewrite would lose first.
    """
    today = (await _db_now(db_session)).date()
    target_date = today + timedelta(days=14)
    goal = await learning.create_goal(
        owner.id,
        title="Machine Learning",
        target_date=target_date,
        progress=35,
        status=LearningGoalStatus.IN_PROGRESS.value,
    )
    title, status, progress, stored_date = await _goal_columns(db_session, goal.id)
    assert (title, status, progress, stored_date) == (
        "Machine Learning",
        "in_progress",
        35,
        target_date,
    )

    rows = await learning_service.generate_learning(owner=owner)

    row = _only(rows, RecommendationType.REVIEW_LEARNING_GOAL)
    due = f"{target_date:%d %b %Y}"
    assert row.title == f"Schedule learning sessions for Machine Learning before {due}"
    assert row.description == (
        "Add three short study sessions for Machine Learning to the coming week, or move "
        "its target date if the scope behind it has changed."
    )
    assert row.reason == (
        f"Machine Learning has a target date of {due}, which is 14 day(s) away, and 35% "
        "recorded progress against it. That percentage is the one on the record, not one "
        "NEXUS estimated."
    )
    assert row.priority == RecommendationPriority.MEDIUM.value
    assert row.entity_type == ENTITY_LEARNING_GOAL
    assert row.entity_id == goal.id
    assert row.risk_id is None, "this suggestion has no raising risk to point back to"


async def test_a_goal_with_ample_progress_is_not_raised(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """The same 14-day horizon, 70% recorded — the rule's own gate, in one figure.

    :data:`GOAL_LOW_PROGRESS_PERCENT` is 50 and this goal is above it. A goal the
    user has recorded most of the way through is a healthy one, and a suggestion
    about it would be the engine talking over them — so the assertion is that
    *nothing at all* is raised, not that a lower-priority row appears.
    """
    today = (await _db_now(db_session)).date()
    await learning.create_goal(
        owner.id,
        title="Machine Learning",
        target_date=today + timedelta(days=14),
        progress=70,
    )

    assert await learning_service.generate_learning(owner=owner) == []


async def test_a_goal_with_no_target_date_is_not_measured_against_one(
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """A date that does not exist cannot be approached, so there is no sentence.

    This is the absence-of-measurement rule rather than a measured zero: the goal
    below is genuinely open and genuinely at 0%, but "you have 0 days left" would
    be a figure NEXUS never read. Declining is the honest answer, and the test
    exists because the alternative — defaulting ``target_date`` to today — is one
    line away and would read perfectly on a card.
    """
    await learning.create_goal(owner.id, title="Rust", progress=0)

    assert await learning_service.generate_learning(owner=owner) == []


async def test_a_goal_whose_target_date_has_already_passed_is_raised_in_its_own_words(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """A passed date is a different fact from a near one, and reads differently.

    Split for the reason :meth:`RecommendationService._rule_review_deadline` splits
    its two deadline cases: "you are five days late" and "you have twenty-five
    days" are not the same sentence, and the reader is owed which one they are
    looking at. Five days past lands in the ``critical`` band of
    :data:`_GOAL_DEADLINE_PRIORITY`, which is the top entry of the ladder rather
    than a band a rule chose for itself.
    """
    today = (await _db_now(db_session)).date()
    await learning.create_goal(
        owner.id,
        title="Rust",
        target_date=today - timedelta(days=5),
        progress=20,
    )

    rows = await learning_service.generate_learning(owner=owner)

    row = _only(rows, RecommendationType.REVIEW_LEARNING_GOAL)
    assert row.title == "Decide the next step for Rust"
    assert row.description == (
        "Decide whether to move the target date, reduce the scope, or record the goal as done."
    )
    assert row.reason == (
        "The target date for Rust passed 5 day(s) ago with 20% recorded progress against "
        "it. That percentage is the one on the record, not one NEXUS estimated."
    )
    assert row.priority == RecommendationPriority.CRITICAL.value


async def test_a_completed_goal_is_never_raised_against_its_own_deadline(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """A finished goal is not outstanding work, whatever its progress column says.

    ``COMPLETED`` is excluded from :data:`OPEN_GOAL_STATUSES` by the Phase 9 metrics
    module for exactly this reason, and this rule reads that same set. A goal
    stamped complete at 40% by some other path is a contradiction the user can
    argue with, not a deadline NEXUS should raise a suggestion about.
    """
    today = (await _db_now(db_session)).date()
    await learning.create_goal(
        owner.id,
        title="Machine Learning",
        target_date=today + timedelta(days=14),
        progress=40,
        status=LearningGoalStatus.COMPLETED.value,
    )

    assert await learning_service.generate_learning(owner=owner) == []


async def test_a_dormant_target_skill_is_raised_with_the_days_idle_and_the_levels(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """Rule 10: Python, 21 days idle, a self-assessed 2/5 against a target of 4/5.

    The fixture writes one activity exactly :data:`DEFAULT_STALE_INACTIVE_DAYS`
    old, so the rule fires *on* the boundary rather than comfortably past it —
    a threshold quietly raised by a day would still pass a 30-day fixture and fail
    here. The constant is asserted against the figure written into the expected
    sentence below, so the two cannot drift apart silently.
    ``evidence_count`` therefore reads 1, which is what makes that sentence say "1
    related learning activity" in the singular, and the levels are read back
    through :func:`_skill_columns` so the sentence is derived from the stored row.

    The two levels put the gap at 2, which is the ``medium`` band of
    :data:`_SKILL_GAP_PRIORITY`, and the current level is quoted with the phrase
    its ``user_defined`` source requires. That phrase is the assertion worth
    making: "a self-assessed 2/5" and a bare "2/5" differ by exactly the honesty
    control the phase is built on.
    """
    assert DEFAULT_STALE_INACTIVE_DAYS == 21
    now = await _db_now(db_session)
    skill = await learning.create_skill(owner.id, name="Python", current_level=2, target_level=4)
    await learning.record_skill_evidence(
        owner.id, skill.id, now - timedelta(days=DEFAULT_STALE_INACTIVE_DAYS)
    )
    name, current, target, source, evidence, last = await _skill_columns(db_session, skill.id)
    assert (name, current, target, source, evidence) == ("Python", 2, 4, "user_defined", 1)
    assert last is not None

    rows = await learning_service.generate_learning(owner=owner)

    row = _only(rows, RecommendationType.REVIVE_TARGET_SKILL)
    # Normalised to UTC because that is the calendar the rule renders the date on.
    # ``last_evidence_at`` comes back labelled with the *connection's* ``TimeZone``,
    # so a bare ``.date()`` here is the server-local day and the sentence quoted
    # below is dated off the other calendar — which for a stamp late in the evening
    # is a day apart from the one the rule wrote.
    idle_on = f"{last.astimezone(UTC).date():%d %b %Y}"
    assert row.title == "Add a practice session for Python"
    assert row.description == (
        "Record one short practice activity for Python, or lower its target level if it "
        "is not one you are aiming at right now."
    )
    assert row.reason == (
        f"The last activity recorded against Python was 21 day(s) ago, on {idle_on}, and "
        f"the record carries a self-assessed 2/5 against a target of 4/5. NEXUS has "
        f"recorded 1 related learning activity against it in total."
    )
    assert row.priority == RecommendationPriority.MEDIUM.value
    assert row.entity_type == ENTITY_SKILL
    assert row.entity_id == skill.id
    assert row.risk_id is None


async def test_a_skill_with_recent_activity_is_not_raised(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """Worked on two days ago: 19 days idle is inside the window, so no nudge.

    The same skill, the same levels and the same gap as the dormant fixture — only
    the recency differs, which is what makes this the complement rather than a
    second example. If the rule were keyed on anything else (the gap, the target
    level, the mere existence of the skill) this would raise and the dormant case
    would prove nothing about what the threshold is for.
    """
    now = await _db_now(db_session)
    skill = await learning.create_skill(owner.id, name="Python", current_level=2, target_level=4)
    await learning.record_skill_evidence(owner.id, skill.id, now - timedelta(days=2))

    assert await learning_service.generate_learning(owner=owner) == []


async def test_a_skill_nothing_has_ever_been_recorded_against_is_not_called_dormant(
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """A brand-new skill has no recency to be stale about, and is declined.

    ``last_activity_at`` is null here, which is a *different fact* from "recorded
    long ago" and is the distinction :mod:`app.services.learning.gaps` exists to
    keep (``available=False`` with a reason, never ``gap=0``). Reading null as zero
    would put "inactive for 0 days" into a sentence — or, worse, fire the nudge the
    instant somebody typed the name, which is the engine inventing the inactivity it
    is about to quote.
    """
    await learning.create_skill(owner.id, name="Rust", current_level=1, target_level=4)

    assert await learning_service.generate_learning(owner=owner) == []


async def test_a_skill_at_its_target_level_is_not_raised(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """Dormant but no longer a *target* skill: there is nothing left to aim at.

    The gap is zero, so the reason the rule would build — two levels and a
    distance — has nothing to say. A skill the user says they have reached is
    dormant, not unfinished, and the two are different facts.
    """
    now = await _db_now(db_session)
    skill = await learning.create_skill(owner.id, name="Python", current_level=4, target_level=4)
    await learning.record_skill_evidence(owner.id, skill.id, now - timedelta(days=90))

    assert await learning_service.generate_learning(owner=owner) == []


async def test_an_account_with_no_learning_data_is_raised_nothing_at_all(
    db_session: AsyncSession,
    risks: RiskRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """No goals, no skills, no risks: an empty list, no rows, no events.

    The cold-start case, and the specific way it fails is a fabricated zero — a
    suggestion table with a row in it for a user who has recorded nothing, which
    reads as "the engine looked and found this". So all four things are asserted:
    the return value, the stored rows, the recommendation history and the event
    feed, exactly as the Phase 7 cold-start test does for risks.
    """
    rows = await learning_service.generate_learning(owner=owner)

    assert rows == []
    assert await _all_rows(db_session, owner) == []
    stored, total = await risks.list_recommendations(owner.id, limit=50, offset=0)
    assert stored == []
    assert total == 0
    assert await _events(db_session, owner, ActivityEvent.RECOMMENDATION_CREATED) == []


async def test_the_learning_sweep_is_a_no_op_when_no_learning_repository_is_wired(
    owner: User,
    db_session: AsyncSession,
) -> None:
    """A degraded collaborator makes the two rules decline, not the service raise.

    The same bargain ``activity=None`` strikes for the reschedule rule: a
    suggestion justified by a figure this service never read is the fabricated
    figure the whole engine is built to avoid, so the sweep returns nothing rather
    than guessing. Exercised through a service wired exactly as the Phase 7
    fixture wires it — no learning repository at all.
    """
    service = RecommendationService(
        RiskRepository(db_session),
        TaskRepository(db_session),
        ProjectRepository(db_session),
        ActivityService(ActivityRepository(db_session)),
    )

    assert await service.generate_learning(owner=owner) == []


async def test_generating_learning_twice_creates_nothing_the_second_time(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """The dedup is the repository's, and it covers the Phase 9 rules unchanged.

    An open suggestion already saying exactly this is not rewritten, so a nightly
    sweep cannot fill the feed with the same card. The count is read back from
    storage rather than from the second call's return value alone: a return value
    of ``[]`` with a row written anyway would pass the first half of this assertion
    and fail the second.
    """
    today = (await _db_now(db_session)).date()
    await learning.create_goal(
        owner.id, title="Machine Learning", target_date=today + timedelta(days=14), progress=35
    )
    assert len(await learning_service.generate_learning(owner=owner)) == 1

    second = await learning_service.generate_learning(owner=owner)

    assert second == []
    assert len(await _all_rows(db_session, owner)) == 1


async def test_a_rejected_learning_suggestion_is_raised_again(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """The open-status index over ``new``/``viewed`` applies here too.

    A rejection is the user's answer, and it has to free the row for the same
    reason it does on a risk-raised suggestion: the user declined and the
    condition did not change, so re-raising the identical wording would be nagging
    rather than noticing. Two rows must therefore exist afterwards, with the first
    still carrying its rejection.
    """
    today = (await _db_now(db_session)).date()
    await learning.create_goal(
        owner.id, title="Machine Learning", target_date=today + timedelta(days=14), progress=35
    )
    first = _only(
        await learning_service.generate_learning(owner=owner),
        RecommendationType.REVIEW_LEARNING_GOAL,
    )
    await learning_service.reject(owner=owner, recommendation_id=first.id)

    second = await learning_service.generate_learning(owner=owner)

    assert len(second) == 1
    assert second[0].id != first.id
    assert [row.status for row in await _all_rows(db_session, owner)] == [
        RecommendationStatus.REJECTED.value,
        RecommendationStatus.NEW.value,
    ]


@pytest.mark.parametrize(
    ("kind", "expected_figures"),
    [
        (
            "goal",
            (
                "14 day(s)",
                "35%",
                "target date of",
                "the record, not one NEXUS estimated",
            ),
        ),
        (
            "skill",
            (
                "21 day(s)",
                "self-assessed 2/5",
                "target of 4/5",
                "1 related learning activity",
            ),
        ),
    ],
)
async def test_every_learning_reason_names_the_figures_behind_it(
    kind: str,
    expected_figures: tuple[str, ...],
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """Both new rules state their own data rather than restating their title.

    :class:`RecommendationDraft` requires *a* digit, which the eight risk rules
    satisfy through the shared score clause. These two have no risk and therefore no
    score clause — inventing one would be a fabricated figure — so the figures in
    the sentence are the only ones there are, and each is asserted by value: the
    days, the user's own percentage or levels, and the sentence that says where the
    number came from.
    """
    now = await _db_now(db_session)
    today = now.date()
    if kind == "goal":
        await learning.create_goal(
            owner.id, title="Machine Learning", target_date=today + timedelta(days=14), progress=35
        )
    else:
        skill = await learning.create_skill(
            owner.id, name="Python", current_level=2, target_level=4
        )
        await learning.record_skill_evidence(owner.id, skill.id, now - timedelta(days=21))

    row = (await learning_service.generate_learning(owner=owner))[0]

    assert row.reason.strip()
    assert any(character.isdigit() for character in row.reason)
    assert row.reason != row.title
    for figure in expected_figures:
        assert figure in row.reason, f"the {kind} rule dropped the figure {figure!r}"


async def test_a_learning_suggestion_is_written_for_one_account_only(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """Ownership is a predicate in the read, and the row carries the caller's id.

    The learning rules sweep an account's goals and skills rather than following a
    pointer out of a risk, so this is the check that the sweep is owner-scoped
    rather than scoped afterwards. A stranger's sweep finds nothing at all, and
    nothing belonging to this account is raised for them.
    """
    today = (await _db_now(db_session)).date()
    await learning.create_goal(
        owner.id, title="Machine Learning", target_date=today + timedelta(days=14), progress=35
    )
    stranger = await register_user(db_session, username="bob", email="bob@nexus.test")

    assert await learning_service.generate_learning(owner=stranger) == []
    assert await learning_service.generate_learning(owner=owner) != []
    stored = await _all_rows(db_session, owner)
    assert len(stored) == 1
    assert stored[0].user_id == owner.id


async def test_no_learning_suggestion_makes_a_claim_about_the_person(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """Every sentence is scanned, and the two Phase 9 bans are asserted whole.

    Beyond the shared :data:`NEUTRALITY_PROHIBITIONS`, the Phase 9 bans are about
    ability and effort: a suggestion may not say the user is weak at a subject, and
    it may not quote hours worked as though they measured the person. Both rules
    are exercised in the same fixture — a goal inside its horizon *and* a dormant
    skill — so the sweep has to get both right before anything is scanned.
    """
    now = await _db_now(db_session)
    today = now.date()
    await learning.create_goal(
        owner.id, title="Machine Learning", target_date=today + timedelta(days=14), progress=35
    )
    skill = await learning.create_skill(owner.id, name="Python", current_level=2, target_level=4)
    await learning.record_skill_evidence(owner.id, skill.id, now - timedelta(days=21))

    rows = await learning_service.generate_learning(owner=owner)

    assert len(rows) == 2
    for row in rows:
        for field, text in (
            ("title", row.title),
            ("description", row.description),
            ("reason", row.reason),
        ):
            found = _prohibitions(text, _PERSON_PROHIBITIONS)
            assert not found, (
                f"the {row.recommendation_type} {field} characterises the person: {found}"
            )
        lowered = row.description.lower()
        for phrase in (
            "has been rescheduled",
            "was rescheduled",
            "has been moved",
            "we rescheduled",
        ):
            assert phrase not in lowered, f"{row.recommendation_type} implies an action was taken"


async def test_a_learning_suggestion_keeps_the_figures_the_rule_read(
    db_session: AsyncSession,
    learning: LearningRepository,
    owner: User,
    learning_service: RecommendationService,
) -> None:
    """The metadata a learning rule derives is stored, because there is no risk row.

    The eight risk-raised suggestions are auditable through the risk they point at.
    These two have ``risk_id`` null, so the provenance has to travel on the
    suggestion itself: the percentage, the distance and the goal's status, with no
    risk score invented for them. Read back through :func:`_stored` rather than
    from the object the repository handed back, because that object is exactly what
    the metadata regression already in this file once failed to persist.
    """
    today = (await _db_now(db_session)).date()
    await learning.create_goal(
        owner.id,
        title="Machine Learning",
        target_date=today + timedelta(days=14),
        progress=35,
        status=LearningGoalStatus.IN_PROGRESS.value,
    )

    raised = await learning_service.generate_learning(owner=owner)
    stored = await _stored(db_session, raised[0].id)

    assert stored.metadata_ == {
        "rule": RecommendationType.REVIEW_LEARNING_GOAL.value,
        "goal_status": "in_progress",
        "progress": 35,
        "days_to_deadline": 14,
    }
    assert stored.risk_id is None
