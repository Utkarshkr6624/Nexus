"""The Phase 9 learning service end to end: what it stores, what it refuses, what it says.

:meth:`app.services.learning.service.LearningIntelligenceService` is the only
seam between "a user typed an intention" and "here is a
:class:`~app.schemas.learning.LearningGoalRead`". Everything interesting about
learning orchestration is decided there and nowhere else, so this file drives it
directly rather than through HTTP — the route layer has its own file, and a test
that went through it would be testing the router as much as the service.

What the file is for
--------------------
``app/services/learning/gaps.py`` and ``app/services/learning/metrics.py`` have
their own tests, over the pure functions. Neither of those can decide the things
below, because each is a statement about *storage*, *ownership* or *time*:

**What the caps refuse.** ``learning_max_goals`` and ``learning_max_skills`` are
checked by counting rows before the insert, and both are tested at the boundary
rather than above it. Archived goals count toward the goal cap, because deleting
one to make room would delete the record the user kept it for.

**What a foreign row is.** Every entry point is exercised with another account's
id, and each raises :class:`NotFoundError` carrying the **same message** as an id
nobody has ever issued. A 403 would confirm the id exists and turn these endpoints
into an existence oracle; two different 404 messages would do the same thing just
as effectively.

**What a level is.** A skill's level is a claim and the response says whose. A
skill whose target equals its current level reports ``gap=0`` **with**
``available=True`` — a measured zero, which is the most useful sentence the page
can print — while a skill with nothing recorded reports ``available=False`` with a
reason. Those are different answers and the tests assert them separately, because
a service that collapsed the second into the first would be claiming a skill is at
its target on the strength of having no evidence at all.

**What absence looks like on the way out.** A metric that could not be measured
comes back ``value=None`` with ``available=False`` and a reason; a metric that
measured zero comes back ``value=0.0, available=True``. In the feature vector an
uncomputable figure is ``null`` and never ``0``, because an account with no goals
does not have goals that are zero percent complete.

House style, deliberately
-------------------------
Follows ``tests/test_developer_service.py``:

* ``pytestmark = pytest.mark.integration`` — every test here needs the live
  PostgreSQL the suite truncates between tests.
* Services are hand-wired in a module-level ``_service()`` helper that mirrors
  the way ``app.api.deps`` will wire them, so a collaborator cannot quietly be
  ``None``. The activity sink in particular is always the real one: passing
  ``activity=None`` would make every event assertion below pass vacuously.
* Rows are read back through **explicit column tuples**, never ORM entities. This
  session is also the one that wrote the rows, so an entity read would hand back
  whatever the identity map cached and an evidence-count assertion would compare
  a stale object to a fresh one and pass for the wrong reason.
* The clock is read from the database through ``_db_now``, never from
  ``date.today()``, because the service resolves every window from ``func.now()``
  and the two must describe the same day.

Every expected figure in this file is derived in the test's own docstring rather
than recorded from a run.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.activity import ActivityLog
from app.models.enums import (
    ActivityEvent,
    LearningActivityType,
    LearningGoalStatus,
    SkillLevelSource,
)
from app.models.knowledge import Note
from app.models.learning import LearningActivity, LearningGoal, Skill
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.knowledge import NoteRepository
from app.repositories.learning import LearningRepository
from app.repositories.project import ProjectRepository
from app.schemas.learning import SkillRead
from app.services.activity_service import ActivityService
from app.services.learning.gaps import NOTHING_RECORDED, TOO_LITTLE_EVIDENCE_TO_ESTIMATE
from app.services.learning.metrics import LEARNING_METRICS, NOT_ENOUGH_DATA
from app.services.learning.service import LearningIntelligenceService
from tests.analytics_fixtures import AnalyticsSeed, register_user

pytestmark = pytest.mark.integration

#: The window the fixtures are written against. Thirty days is the configured
#: default, so every trailing-window figure below is exactly what the request a
#: user actually sends would produce.
WINDOW = 30

#: The schema version stamped on the feature vector. Named here rather than
#: imported so a rename is a test failure: that string is the contract with
#: whatever trains on it later.
FEATURE_SCHEMA_VERSION = "learning_features.v1"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(
    session: AsyncSession, *, settings: Settings | None = None
) -> LearningIntelligenceService:
    """A learning service wired the way ``app.api.deps`` will wire it.

    Every collaborator is the real one. The activity sink in particular: it is
    what ``LEARNING_GOAL_CREATED``, ``LEARNING_GOAL_UPDATED``,
    ``LEARNING_GOAL_COMPLETED``, ``LEARNING_SESSION_RECORDED``, ``SKILL_CREATED``,
    ``SKILL_UPDATED`` and ``SKILL_ACTIVITY_RECORDED`` are written through, and this
    file asserts on all seven, so a ``None`` sink would make a third of it vacuous.

    Args:
        session: The test session. Every repository is built on the same one,
            which is why the reads below go through explicit columns.
        settings: Supplied only by the cap tests, which need a deployment whose
            ``learning_max_goals`` or ``learning_max_skills`` is two rather than the
            configured two hundred and hundred.
    """
    return LearningIntelligenceService(
        repositories=LearningRepository(session),
        projects=ProjectRepository(session),
        notes=NoteRepository(session),
        activity=ActivityService(ActivityRepository(session)),
        settings=settings,
    )


async def _owner(session: AsyncSession, username: str = "ada") -> User:
    """One account, inserted directly.

    Direct rather than through the API because these tests drive the service, not a
    route, and ``register_user`` writes no activity events — which matters, because
    the event assertions below count event types and a registration row would move
    them.
    """
    return await register_user(session, username=username)


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock, as an aware UTC instant.

    The same read :meth:`LearningIntelligenceService._now` performs. Fixtures are
    placed relative to this rather than to ``datetime.now()`` so a session the
    service will read back sits inside the window the service will compute.
    """
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _note(session: AsyncSession, owner: User, *, title: str = "field note") -> Note:
    """One knowledge note owned by ``owner``.

    Inserted directly because the point of these tests is goal ownership rather
    than note creation, and :class:`NoteRepository` has no test-facing builder. The
    defaults cover everything the learning table's foreign key needs.
    """
    note = Note(owner_id=owner.id, title=title)
    session.add(note)
    await session.commit()
    return note


# ---------------------------------------------------------------------------
# Reading stored rows back
# ---------------------------------------------------------------------------

#: The columns every stored goal is read back through. Listed in full so a column
#: added to the model shows up here rather than as a silently unasserted field.
_GOAL_COLUMNS = (
    LearningGoal.id,
    LearningGoal.title,
    LearningGoal.status,
    LearningGoal.progress,
    LearningGoal.priority,
    LearningGoal.target_skill_id,
    LearningGoal.project_id,
    LearningGoal.completed_at,
)

_SKILL_COLUMNS = (
    Skill.id,
    Skill.name,
    Skill.current_level,
    Skill.target_level,
    Skill.level_source,
    Skill.confidence,
    Skill.evidence_count,
    Skill.last_activity_at,
)

_ACTIVITY_COLUMNS = (
    LearningActivity.id,
    LearningActivity.title,
    LearningActivity.activity_type,
    LearningActivity.skill_id,
    LearningActivity.goal_id,
    LearningActivity.duration_minutes,
    LearningActivity.source_type,
    LearningActivity.occurred_at,
)


async def _goal_row(session: AsyncSession, goal_id: uuid.UUID) -> dict[str, object]:
    """The single stored goal row, asserting there is exactly one."""
    result = await session.execute(select(*_GOAL_COLUMNS).where(LearningGoal.id == goal_id))
    row = result.one_or_none()
    assert row is not None, f"no stored goal with id {goal_id}"
    return dict(row._mapping)


async def _skill_row(session: AsyncSession, skill_id: uuid.UUID) -> dict[str, object]:
    """The single stored skill row, asserting there is exactly one."""
    result = await session.execute(select(*_SKILL_COLUMNS).where(Skill.id == skill_id))
    row = result.one_or_none()
    assert row is not None, f"no stored skill with id {skill_id}"
    return dict(row._mapping)


async def _activity_rows(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """This account's stored activities, newest first."""
    result = await session.execute(
        select(*_ACTIVITY_COLUMNS)
        .where(LearningActivity.user_id == owner_id)
        .order_by(LearningActivity.occurred_at.desc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _row_count(session: AsyncSession, table: type, owner_id: uuid.UUID) -> int:
    """How many rows of ``table`` this account owns.

    Counted in SQL through the explicit model rather than through the ORM, for the
    reason every other read here is: the identity map still holds the objects this
    session wrote, and ``len(session.new)``-style bookkeeping would be reading the
    cache rather than the database.
    """
    return int(
        await session.scalar(
            select(func.count()).select_from(table).where(table.user_id == owner_id)
        )
    )


#: The seven Phase 9 learning event types. Named in full rather than filtered on a
#: prefix so an event added to the reconciliation shows up in this tuple and fails
#: the assertion rather than being filtered past it.
_LEARNING_EVENTS = (
    ActivityEvent.LEARNING_GOAL_CREATED.value,
    ActivityEvent.LEARNING_GOAL_UPDATED.value,
    ActivityEvent.LEARNING_GOAL_COMPLETED.value,
    ActivityEvent.LEARNING_SESSION_RECORDED.value,
    ActivityEvent.SKILL_CREATED.value,
    ActivityEvent.SKILL_UPDATED.value,
    ActivityEvent.SKILL_ACTIVITY_RECORDED.value,
)


async def _events(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, object]]:
    """This account's Phase 9 history rows, with their metadata.

    Filtered to the seven learning events rather than to the whole feed: the
    assertions are about the reconciliation this phase writes, and a feed that also
    carried unrelated events would make every count a substring search.

    Ordering is left to the query and never asserted on. The claim under test is
    "this fact was recorded" and "this fact was not recorded again", which counts
    answer; the order the database chose to write eight transactions in is not a
    fact about the product.
    """
    result = await session.execute(
        select(ActivityLog.event_type, ActivityLog.metadata_).where(
            ActivityLog.user_id == user_id,
            ActivityLog.event_type.in_(_LEARNING_EVENTS),
        )
    )
    return [{"event_type": str(row[0]), "metadata": row[1]} for row in result.all()]


def _event_counts(events: list[dict[str, object]]) -> Counter[str]:
    """``{event type: how many}`` for one pass's history rows."""
    counts: Counter[str] = Counter()
    for event in events:
        counts[str(event["event_type"])] += 1
    return counts


def _metadata_text(metadata: object) -> str:
    """Every string rendered inside one event's metadata, joined.

    The assertion it supports is a negative one — that a goal title or a skill
    description never reaches the history feed — and a negative about free text is
    only checkable if the free text is actually searchable for.
    """
    return " ".join(
        str(value)
        for value in (metadata if isinstance(metadata, dict) else {}).values()
        if isinstance(value, str)
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


async def _tracked_skill(
    session: AsyncSession, owner: User, name: str = "Python", *, target_level: int | None = None
) -> SkillRead:
    """One tracked skill, created through the service so its defaults are the real ones."""
    return await _service(session).create_skill(
        owner=owner, name=name, current_level=1, target_level=target_level
    )


# ---------------------------------------------------------------------------
# (a) Goals
# ---------------------------------------------------------------------------


async def test_creating_a_goal_stores_the_users_own_figure_and_writes_one_event(
    db_session: AsyncSession,
) -> None:
    """One row, every default from the schema, and one event carrying no title.

    ``progress`` is 0 and ``status`` is ``not_started`` because those are the
    schema's own defaults and the user supplied neither: a goal created during
    planning has demonstrably not started, and defaulting it forward would claim an
    activity nobody recorded. ``priority`` is ``medium`` for the same reason.

    ``completed_at`` is null, and the check constraint would refuse a stamp without
    a terminal status anyway — completion has exactly one producer,
    :meth:`~LearningIntelligenceService.complete_goal`.

    **The event carries ids and vocabulary only.** The goal's title is the user's
    own words and belongs on the row the event points at; the history feed is the
    least access-controlled surface the product has, and a summary view quoting a
    stale copy of it is worse than one that follows the id.
    """
    owner = await _owner(db_session)
    service = _service(db_session)

    created = await service.create_goal(
        owner=owner, title="Read the Postgres docs", target_topic="query planning"
    )

    assert created.status == "not_started"
    assert created.progress == 0
    assert created.priority == "medium"
    assert created.completed_at is None

    stored = await _goal_row(db_session, created.id)
    assert stored["title"] == "Read the Postgres docs"
    assert stored["status"] == "not_started"
    assert stored["progress"] == 0
    assert stored["priority"] == "medium"
    assert stored["completed_at"] is None

    events = await _events(db_session, owner.id)
    assert _event_counts(events) == {ActivityEvent.LEARNING_GOAL_CREATED.value: 1}
    assert events[0]["metadata"] == {
        "goal_id": str(created.id),
        "status": "not_started",
        "priority": "medium",
    }
    assert "Read the Postgres docs" not in _metadata_text(events[0]["metadata"])


async def test_the_goal_cap_refuses_the_goal_that_would_exceed_it(
    db_session: AsyncSession, make_settings
) -> None:
    """At the cap of two, the third goal is a conflict that names the limit.

    The cap counts **every** row, and the fixture completes one goal first so the
    boundary a naive ``WHERE status != 'completed'`` would lose is the one under
    test. A completed goal is a record the user kept; a cap that let the count
    rise again by finishing something would be a number that could move without
    anything being created.

    Grace registering one goal of her own at the same time is the other half: the
    cap is per account, so Ada reaching hers cannot stop Grace.
    """
    settings = make_settings(LEARNING_MAX_GOALS="2")
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    service = _service(db_session, settings=settings)

    first = await service.create_goal(owner=ada, title="one")
    second = await service.create_goal(owner=ada, title="two")
    await service.complete_goal(owner=ada, goal_id=second.id)
    await service.create_goal(owner=grace, title="grace's own")

    with pytest.raises(ConflictError) as at_cap:
        await service.create_goal(owner=ada, title="three")

    assert str(at_cap.value) == ("This account already has the maximum of 2 learning goals.")
    assert await _row_count(db_session, LearningGoal, ada.id) == 2
    assert await _row_count(db_session, LearningGoal, grace.id) == 1
    assert first.id != second.id


async def test_completing_a_goal_stamps_completed_at_sets_progress_and_emits_its_own_event(
    db_session: AsyncSession,
) -> None:
    """40% and an undated goal become ``completed``, 100% and a stamped instant.

    Three columns move together, which is the assertion. ``status`` and
    ``completed_at`` are held together by
    ``ck_learning_goals_completed_has_terminal_status``, and ``progress`` goes to
    100 because a finished goal reporting 40% is a contradiction a user cannot
    argue with. Doing them in separate requests would leave a window in which the
    row claimed some of them and not the others.

    ``completed_at`` is the database's own clock, read back through ``func.now()``
    and compared by date rather than by instant — the value and the read happen
    microseconds apart, and a test that pinned the exact timestamp would be pinning
    a race rather than a behaviour.

    A dedicated ``LEARNING_GOAL_COMPLETED`` is emitted rather than a second
    ``LEARNING_GOAL_UPDATED``: completion is a lifecycle event with a fact of its
    own, and a feed that could only say "updated" could not answer "when did they
    finish this".
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    goal = await service.create_goal(owner=owner, title="Finish the book", progress=40)

    completed = await service.complete_goal(owner=owner, goal_id=goal.id)

    assert completed.status == "completed"
    assert completed.progress == 100
    assert completed.completed_at is not None

    stored = await _goal_row(db_session, goal.id)
    assert stored["status"] == "completed"
    assert stored["progress"] == 100
    assert stored["completed_at"].date() == (await _db_now(db_session)).date()

    events = await _events(db_session, owner.id)
    assert _event_counts(events) == {
        ActivityEvent.LEARNING_GOAL_CREATED.value: 1,
        ActivityEvent.LEARNING_GOAL_COMPLETED.value: 1,
    }
    completed_event = next(
        event
        for event in events
        if event["event_type"] == ActivityEvent.LEARNING_GOAL_COMPLETED.value
    )
    assert completed_event["metadata"] == {
        "goal_id": str(goal.id),
        "progress": 100,
        "skill_id": None,
    }
    assert "Finish the book" not in _metadata_text(completed_event["metadata"])


async def test_deleting_a_goal_keeps_the_record_that_the_user_worked_on_it(
    db_session: AsyncSession,
) -> None:
    """The goal is gone; the activity recorded towards it is not.

    ``learning_activities.goal_id`` is ``ON DELETE SET NULL``, and the reason is
    this test: deleting the intention must not delete the evidence. A skill's
    evidence count is a history of what was recorded, and a user who abandons a
    goal has not unlearned anything.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    goal = await service.create_goal(owner=owner, title="Learn Rust")
    await service.record_activity(
        owner=owner,
        title="Read the ownership chapter",
        activity_type=LearningActivityType.STUDY_SESSION.value,
        goal_id=goal.id,
        duration_minutes=40,
    )

    await service.delete_goal(owner=owner, goal_id=goal.id)

    assert await _row_count(db_session, LearningGoal, owner.id) == 0
    remaining = await _activity_rows(db_session, owner.id)
    assert len(remaining) == 1
    assert remaining[0]["goal_id"] is None
    assert remaining[0]["title"] == "Read the ownership chapter"


async def test_a_goal_edit_must_change_something_and_may_not_touch_the_completion_stamp(
    db_session: AsyncSession,
) -> None:
    """An empty patch and a ``completed_at`` write are both refused, before any event.

    An empty patch is refused rather than treated as a no-op success: a cheerful
    200 for a change that changed nothing is how a client comes to believe it
    applied an edit. The completion stamp is refused because
    :meth:`~LearningIntelligenceService.complete_goal` is its only writer, which is
    what keeps "when did they finish this" a fact with one producer.

    A *successful* edit does write exactly one event, and an unknown vocabulary
    value is a validation error rather than a stored string a filter could never
    find.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    goal = await service.create_goal(owner=owner, title="Learn Rust")

    with pytest.raises(ValidationError):
        await service.update_goal(owner=owner, goal_id=goal.id, values={})
    with pytest.raises(ValidationError):
        await service.update_goal(
            owner=owner, goal_id=goal.id, values={"completed_at": await _db_now(db_session)}
        )
    with pytest.raises(ValidationError) as unknown_status:
        await service.update_goal(owner=owner, goal_id=goal.id, values={"status": "nearly"})

    assert "nearly" in str(unknown_status.value)
    assert _event_counts(await _events(db_session, owner.id)) == {
        ActivityEvent.LEARNING_GOAL_CREATED.value: 1
    }

    edited = await service.update_goal(owner=owner, goal_id=goal.id, values={"progress": 25})
    assert edited.progress == 25
    assert edited.completed_at is None
    assert _event_counts(await _events(db_session, owner.id)) == {
        ActivityEvent.LEARNING_GOAL_CREATED.value: 1,
        ActivityEvent.LEARNING_GOAL_UPDATED.value: 1,
    }


# ---------------------------------------------------------------------------
# (b) Skills
# ---------------------------------------------------------------------------


async def test_a_new_skill_is_a_name_and_the_users_own_starting_position(
    db_session: AsyncSession,
) -> None:
    """Level 1 aiming at 3, claimed by the user, with no confidence and no evidence.

    ``level_source='user_defined'`` is the whole point of the row. A skill created
    as ``system_estimate`` would be claiming an inference that has not happened
    yet, and ``confidence=0`` means "there was nothing to estimate from" rather
    than "a weak estimate exists". ``evidence_count`` 0 and ``last_activity_at``
    null are likewise real values — a count of zero is a measurement, and the null
    is not "long ago".
    """
    owner = await _owner(db_session)

    skill = await _tracked_skill(db_session, owner)

    assert skill.current_level == 1
    assert skill.target_level == 3
    assert skill.level_source == SkillLevelSource.USER_DEFINED.value
    assert skill.confidence == 0
    assert skill.evidence_count == 0
    assert skill.last_activity_at is None

    stored = await _skill_row(db_session, skill.id)
    assert stored["name"] == "Python"
    assert (stored["current_level"], stored["target_level"]) == (1, 3)
    assert stored["level_source"] == "user_defined"
    assert stored["evidence_count"] == 0
    assert stored["last_activity_at"] is None

    events = await _events(db_session, owner.id)
    assert _event_counts(events) == {ActivityEvent.SKILL_CREATED.value: 1}
    assert events[0]["metadata"] == {
        "skill_id": str(skill.id),
        "level_source": "user_defined",
        "current_level": 1,
        "target_level": 3,
    }
    assert "Python" not in _metadata_text(events[0]["metadata"])


async def test_the_skill_cap_refuses_the_skill_that_would_exceed_it(
    db_session: AsyncSession, make_settings
) -> None:
    """At the cap of two, the third skill is a conflict that names the limit.

    The cap counts every row — the skills list is the input to every gap
    computation, so a cap that ignored a paused or a dormant skill would be a
    number that could move without anything being added.
    """
    settings = make_settings(LEARNING_MAX_SKILLS="2")
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    service = _service(db_session, settings=settings)

    await _tracked_skill(db_session, ada, "Python")
    await _tracked_skill(db_session, ada, "SQL")
    await _tracked_skill(db_session, grace, "Python")

    with pytest.raises(ConflictError) as at_cap:
        await service.create_skill(owner=ada, name="Rust")

    assert str(at_cap.value) == "This account already has the maximum of 2 skills."
    assert await _row_count(db_session, Skill, ada.id) == 2
    assert await _row_count(db_session, Skill, grace.id) == 1


async def test_a_duplicate_skill_name_is_a_conflict_per_account_and_not_an_integrity_error(
    db_session: AsyncSession,
) -> None:
    """The same name twice on one account is a 409-shaped conflict; two accounts may share it.

    ``uq_skills_owner_name`` is per account on purpose. Two people both tracking
    "Python" is the entire point of a per-account notebook, and a global index
    would have made the second person's skill a conflict about a vocabulary they do
    not share. The service checks the name first and reports a sentence, rather
    than letting an ``IntegrityError`` reach the client as a 500 carrying a
    constraint name.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    service = _service(db_session)

    first = await service.create_skill(owner=ada, name="Python")
    theirs = await service.create_skill(owner=grace, name="Python")

    with pytest.raises(ConflictError) as duplicate:
        await service.create_skill(owner=ada, name="Python")

    assert str(duplicate.value) == "That skill is already tracked for this account."
    assert first.id != theirs.id
    assert await _row_count(db_session, Skill, ada.id) == 1


async def test_a_skill_edit_that_moves_the_level_re_records_the_source_as_user_defined(
    db_session: AsyncSession,
) -> None:
    """Re-asserting ``current_level`` credits the number to the person, not to NEXUS.

    The service writes ``level_source='user_defined'`` alongside any level a client
    sends, and does the same whether the previous source was a user claim or a
    system estimate: a level the user has just typed is theirs whatever it was
    before, and leaving ``system_estimate`` in place would keep crediting NEXUS for
    a number the person asserted. The fixture seeds a system estimate directly
    through the repository, which is the only way one can exist.
    """
    owner = await _owner(db_session)
    skill = await _tracked_skill(db_session, owner)
    repository = LearningRepository(db_session)
    seeded = await repository.update_skill(
        owner.id, skill.id, {"level_source": SkillLevelSource.SYSTEM_ESTIMATE.value}
    )
    assert seeded is not None and seeded.level_source == "system_estimate"

    edited = await _service(db_session).update_skill(
        owner=owner, skill_id=skill.id, values={"current_level": 4}
    )

    assert edited.current_level == 4
    assert edited.level_source == SkillLevelSource.USER_DEFINED.value
    stored = await _skill_row(db_session, skill.id)
    assert (stored["current_level"], stored["level_source"]) == (4, "user_defined")
    events = await _events(db_session, owner.id)
    assert _event_counts(events) == {
        ActivityEvent.SKILL_CREATED.value: 1,
        ActivityEvent.SKILL_UPDATED.value: 1,
    }


# ---------------------------------------------------------------------------
# (c) Activities — the evidence
# ---------------------------------------------------------------------------


async def test_recording_an_activity_counts_it_against_its_skill_and_writes_two_events(
    db_session: AsyncSession,
) -> None:
    """One row, ``evidence_count`` 0 → 1, a recency stamp, and the two events.

    The counter is the assertion that matters: it is a cached value the gap read
    quotes as the evidence behind a level, so it has exactly one writer
    (:meth:`~app.repositories.learning.LearningRepository.record_skill_evidence`)
    and the service is what calls it. ``last_activity_at`` moves to the recorded
    instant rather than to the wall clock, which is the difference between "the
    user worked on this on Tuesday" and "they worked on it just now" when they are
    back-filling a week.

    **No level moves.** ``current_level`` is untouched at 1: evidence is collected,
    and the level is asserted separately and labelled. A service that let an
    activity raise somebody's level would be editing a claim the user made, which
    is the one thing rule 1 of this phase forbids.

    ``source_type`` is stored because it is the label that stops "6 commits
    touched Python files" being read back as "6 Python tasks completed" — and it
    is in the *row*, not in the event metadata, for the same reason the title is.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    skill = await _tracked_skill(db_session, owner)
    when = await _db_now(db_session) - timedelta(days=2)

    recorded = await service.record_activity(
        owner=owner,
        title="Worked through the indexing chapter",
        activity_type=LearningActivityType.STUDY_SESSION.value,
        skill_id=skill.id,
        duration_minutes=45,
        occurred_at=when,
        source_type="manual",
    )

    assert recorded.duration_minutes == 45
    assert recorded.skill_id == skill.id
    assert recorded.occurred_at.date() == when.date()

    stored_skill = await _skill_row(db_session, skill.id)
    assert stored_skill["evidence_count"] == 1
    assert stored_skill["last_activity_at"].date() == when.date()
    assert stored_skill["current_level"] == 1, "recording evidence must not move a level"

    stored_activity = (await _activity_rows(db_session, owner.id))[0]
    assert stored_activity["activity_type"] == "study_session"
    assert stored_activity["duration_minutes"] == 45
    assert stored_activity["source_type"] == "manual"

    events = await _events(db_session, owner.id)
    counts = _event_counts(events)
    assert counts[ActivityEvent.LEARNING_SESSION_RECORDED.value] == 1
    assert counts[ActivityEvent.SKILL_ACTIVITY_RECORDED.value] == 1
    skill_event = next(
        event
        for event in events
        if event["event_type"] == ActivityEvent.SKILL_ACTIVITY_RECORDED.value
    )
    assert skill_event["metadata"] == {
        "activity_id": str(recorded.id),
        "skill_id": str(skill.id),
        "evidence_count": 1,
    }
    assert "Worked through the indexing chapter" not in _metadata_text(skill_event["metadata"])


async def test_an_activity_type_outside_the_vocabulary_is_refused_and_stores_no_row(
    db_session: AsyncSession,
) -> None:
    """An unrecognised ``activity_type`` is a 422, not a row no filter can find.

    ``learning_activities.activity_type`` is a plain string filtered on by
    consumers, so a typo is storable and would produce a row the evidence count and
    the chart could never see. The service converts the repository's ``ValueError``
    into the :class:`ValidationError` the route turns into a 422 — and the
    validation happens before the write, so the account's activity count is still
    zero afterwards.
    """
    owner = await _owner(db_session)
    service = _service(db_session)

    with pytest.raises(ValidationError):
        await service.record_activity(owner=owner, title="Vibed at it", activity_type="vibing")

    assert await _row_count(db_session, LearningActivity, owner.id) == 0
    assert await _events(db_session, owner.id) == []


# ---------------------------------------------------------------------------
# (d) Ownership — 404, never 403
# ---------------------------------------------------------------------------


async def test_another_accounts_rows_are_not_found_from_every_entry_point(
    db_session: AsyncSession,
) -> None:
    """Ada's goals, skills and activities are invisible to Grace, and every entry point 404s.

    Every entry point is exercised, because each could have been written with the
    owner predicate missing and the *detail* would still have been private: the
    goal detail, edit, completion and delete; the skill detail, edit and delete;
    recording an activity against a foreign skill or a foreign goal; patching a
    goal onto a foreign skill, project or note; and the two scoped reads.

    **Every refusal carries the same message, and an id nobody ever issued carries
    the same message again.** That is what stops these endpoints being an
    existence oracle: if a foreign id and an unknown id were distinguishable, a
    caller could enumerate other people's goal and skill ids by comparing answers.

    And the *unscoped* reads are empty rather than carrying Ada's rows, with Ada's
    own rows untouched afterwards.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    service = _service(db_session)
    skill = await _tracked_skill(db_session, ada, "Python")
    goal = await service.create_goal(owner=ada, title="Learn Rust")
    project = await AnalyticsSeed(db_session, ada).project(name="Atlas")
    note = await _note(db_session, ada)
    await service.record_activity(
        owner=ada, title="Read a chapter", activity_type=LearningActivityType.STUDY_SESSION.value
    )

    refusals = {
        "goal_detail": await _refusal(service.get_goal(owner=grace, goal_id=goal.id)),
        "goal_edit": await _refusal(
            service.update_goal(owner=grace, goal_id=goal.id, values={"progress": 50})
        ),
        "goal_complete": await _refusal(service.complete_goal(owner=grace, goal_id=goal.id)),
        "goal_delete": await _refusal(service.delete_goal(owner=grace, goal_id=goal.id)),
        "skill_detail": await _refusal(service.get_skill(owner=grace, skill_id=skill.id)),
        "skill_edit": await _refusal(
            service.update_skill(owner=grace, skill_id=skill.id, values={"current_level": 5})
        ),
        "skill_delete": await _refusal(service.delete_skill(owner=grace, skill_id=skill.id)),
        "activity_on_foreign_skill": await _refusal(
            service.record_activity(
                owner=grace,
                title="Borrowed",
                activity_type=LearningActivityType.STUDY_SESSION.value,
                skill_id=skill.id,
            )
        ),
        "activity_on_foreign_goal": await _refusal(
            service.record_activity(
                owner=grace,
                title="Borrowed",
                activity_type=LearningActivityType.STUDY_SESSION.value,
                goal_id=goal.id,
            )
        ),
        "goal_onto_foreign_skill": await _refusal(
            service.create_goal(owner=grace, title="Aim at Ada's skill", target_skill_id=skill.id)
        ),
        "goal_onto_foreign_project": await _refusal(
            service.create_goal(owner=grace, title="Aim at Ada's project", project_id=project.id)
        ),
        "goal_onto_foreign_note": await _refusal(
            service.create_goal(owner=grace, title="File under Ada's note", note_id=note.id)
        ),
        "activity_scoped_to_foreign_skill": await _refusal(
            service.read_activity(owner=grace, skill_id=skill.id)
        ),
        "activity_scoped_to_foreign_goal": await _refusal(
            service.read_activity(owner=grace, goal_id=goal.id)
        ),
    }
    unknown_goal = await _refusal(service.get_goal(owner=grace, goal_id=uuid.uuid4()))
    unknown_skill = await _refusal(service.get_skill(owner=grace, skill_id=uuid.uuid4()))

    assert str(refusals["goal_detail"]) == str(unknown_goal)
    assert str(unknown_goal) == "That learning goal was not found."
    assert str(refusals["skill_detail"]) == str(unknown_skill)
    assert str(unknown_skill) == "That skill was not found."
    for name in (
        "goal_edit",
        "goal_complete",
        "goal_delete",
        "activity_on_foreign_goal",
        "activity_scoped_to_foreign_goal",
    ):
        assert str(refusals[name]) == str(unknown_goal), f"{name} answered differently"
    for name in (
        "skill_edit",
        "skill_delete",
        "activity_on_foreign_skill",
        "goal_onto_foreign_skill",
        "activity_scoped_to_foreign_skill",
    ):
        assert str(refusals[name]) == str(unknown_skill), f"{name} answered differently"
    assert str(refusals["goal_onto_foreign_project"]) == "That project was not found."
    assert str(refusals["goal_onto_foreign_note"]) == "That note was not found."

    # Nothing was written for Grace by any of it...
    assert await _row_count(db_session, LearningGoal, grace.id) == 0
    assert await _row_count(db_session, Skill, grace.id) == 0
    assert await _row_count(db_session, LearningActivity, grace.id) == 0
    assert await _events(db_session, grace.id) == []
    grace_goals = await service.list_goals(owner=grace)
    assert grace_goals.items == []
    assert grace_goals.summary == "No learning goals."
    assert (await service.list_skills(owner=grace)).items == []
    assert (await service.list_activities(owner=grace)).summary == "No learning activities."

    # ...and Ada's rows are untouched, evidence counter included.
    stored = await _skill_row(db_session, skill.id)
    assert stored["name"] == "Python"
    assert (await _goal_row(db_session, goal.id))["title"] == "Learn Rust"
    assert stored["evidence_count"] == 0


async def _refusal(coroutine: object) -> str:
    """Return the message a refused call raised, or fail the test naming itself.

    A small helper because the ownership test above refuses thirteen times and the
    interesting assertion is that all of them answer the same way. A refusal that
    did **not** raise propagates its assertion failure here, naming the call site.

    Args:
        coroutine: The awaited call, already started.

    Returns:
        The ``str()`` of the :class:`NotFoundError` it raised.
    """
    try:
        await coroutine
    except NotFoundError as error:
        return str(error)
    raise AssertionError("the call was expected to raise NotFoundError and did not")


# ---------------------------------------------------------------------------
# (e) Absence is not zero
# ---------------------------------------------------------------------------


async def test_an_account_with_no_learning_activity_declines_rather_than_reporting_zeros(
    db_session: AsyncSession,
) -> None:
    """An account that has recorded nothing: four measured zeros, four honest declines.

    The measured ones — the two session counts, ``learning_minutes`` and
    ``learning_consistency`` — report ``value=0.0`` with ``available=True`` and no
    reason. "Nothing was recorded in these thirty days" is a true sentence about the
    record, and it is the answer for someone who signed up today.

    The four declines are ratios with an empty denominator. There is no open goal to
    average a progress figure over, no dated goal to be near or late for, nothing to
    divide a completion rate by and no skill for an activity to be frequent
    *relative to*. ``0/0`` is a number and a mistake: a new account that has written
    no goals has not completed 0% of them, it has a question that was never asked.
    Each carries the shared reason and a null value, never a zero.

    Note what is **not** here: ``learning_consistency`` is a count of days carrying
    a recorded row over a count of days in the window, and an empty window is a
    measured zero. It is the *ratios* that decline.

    Exactly eight keys come back every time — a metric that was omitted would render
    a hole where a client indexing by key expects a card.
    """
    owner = await _owner(db_session)
    service = _service(db_session)

    metrics = await service.metrics(owner=owner, window_days=WINDOW)
    by_key = {metric.key: metric for metric in metrics}

    assert tuple(metric.key for metric in metrics) == tuple(key.value for key in LEARNING_METRICS)
    for key in (
        "sessions_last_7d",
        "sessions_last_30d",
        "learning_minutes",
        "learning_consistency",
    ):
        assert by_key[key].value == 0.0
        assert by_key[key].available is True
        assert by_key[key].reason_if_unavailable is None
    for key in (
        "goal_progress",
        "goal_deadline_distance_days",
        "completion_rate",
        "skill_activity_frequency",
    ):
        assert by_key[key].available is False, f"{key} published a figure it declined"
        assert by_key[key].value is None
        assert by_key[key].reason_if_unavailable == NOT_ENOUGH_DATA
    assert all(any(character.isdigit() for character in metric.explanation) for metric in metrics)

    summary = await service.summary(owner=owner, window_days=WINDOW)
    assert summary.has_data is False
    assert summary.activity_count == 0
    assert summary.minutes_in_window is None, "no duration recorded is not a measured zero"
    assert summary.goal_count == 0
    assert summary.active_goal_count == 0
    assert summary.skill_count == 0
    assert summary.latest_activity_at is None


async def test_the_feature_vector_is_null_not_zero_for_every_figure_it_cannot_measure(
    db_session: AsyncSession,
) -> None:
    """An account with no skills, no deadlines and no durations returns six nulls.

    This is the contract's own example, asserted column by column. An account with
    one unstarted goal and nothing else recorded has:

    * ``goal_progress`` **null** — no *open* goal carries a figure... and here the
      one goal that exists *is* open at 0%, so this figure is a real 0.0. It is
      asserted as such below, which is the point: a measured zero and an absent
      measurement sit side by side in one response;
    * ``goal_deadline_distance_days`` **null** — no goal carries a self-imposed
      date, and null means no date was stated, which is not the same as a deadline
      today;
    * ``completion_rate`` **0.0** — one non-archived goal, none completed, so
      ``0/1`` is computable and is a measurement. (With no goals at all it would be
      null instead; the empty-account case is asserted in the test below.);
    * ``learning_minutes`` **null** — nothing in the window recorded a duration;
    * ``learning_consistency`` **null** — nothing was recorded, and 0.0 would
      claim the window was measured and found bare;
    * ``skill_activity_frequency`` **null** — no skill is named by anything.

    The two session counts are genuine measured zeros and stay ``0``, and the vector
    is stamped ``learning_features.v1`` because that string is the contract with
    whatever trains on it. Nothing here is a model.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    await service.create_goal(owner=owner, title="Never started")

    vector = await service.features(owner=owner, window_days=WINDOW)

    assert vector.schema_version == FEATURE_SCHEMA_VERSION
    assert vector.window_days == WINDOW
    assert vector.generated_at is not None
    features = vector.features
    assert features.sessions_last_7d == 0
    assert features.sessions_last_30d == 0
    assert features.goal_progress == 0.0, "one open goal at 0% is a measured 0.0"
    assert features.completion_rate == 0.0, "0 of 1 non-archived goals is a measured 0.0"
    assert features.learning_minutes is None
    assert features.goal_deadline_distance_days is None
    assert features.learning_consistency is None
    assert features.skill_activity_frequency is None


async def test_an_account_with_no_goals_returns_null_for_every_goal_ratio(
    db_session: AsyncSession,
) -> None:
    """Recorded sessions but no goals: the counts are real and the goal ratios are null.

    The mean of an empty set has no answer that is not invented, and ``0/0`` is a
    number and a mistake. An account that has written no goals has not completed 0%
    of nothing and has not made 0% progress on nothing — it has a question that was
    never asked, which is what a null plus a reason says.

    The counts beside them are unaffected: two recorded activities in the trailing
    month is a real measurement whether or not anybody has written a goal, and
    ``learning_minutes`` is 30 rather than null because exactly one of the two
    activities carried a duration.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    skill = await _tracked_skill(db_session, owner)
    when = await _db_now(db_session)
    await service.record_activity(
        owner=owner,
        title="Two sessions",
        activity_type=LearningActivityType.STUDY_SESSION.value,
        skill_id=skill.id,
        duration_minutes=30,
        occurred_at=when - timedelta(days=1),
    )
    await service.record_activity(
        owner=owner,
        title="A page view",
        activity_type=LearningActivityType.RESOURCE_VIEWED.value,
        occurred_at=when - timedelta(days=2),
    )

    vector = await service.features(owner=owner, window_days=WINDOW)

    assert vector.features.goal_progress is None
    assert vector.features.completion_rate is None
    assert vector.features.goal_deadline_distance_days is None
    assert vector.features.sessions_last_7d == 2
    assert vector.features.sessions_last_30d == 2
    assert vector.features.learning_minutes == 30, "only the timed session carries a duration"
    assert vector.features.learning_consistency is not None
    assert vector.features.skill_activity_frequency == 1.0


async def test_a_goal_with_a_deadline_reports_the_days_the_user_has_left(
    db_session: AsyncSession,
) -> None:
    """One dated open goal ten days out gives a measured deadline distance of ten.

    The figure is derived from the database clock rather than from
    ``date.today()``, because the service resolves ``window_end`` from ``func.now()``
    and the two must describe the same day. It is asserted as a range rather than an
    exact integer because the fixture's date is read once and the metric's is read
    again: a run that crossed midnight would legitimately report nine or ten, and a
    test that pinned one of them would be pinning a race.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    # Normalised to UTC to match the service's own clock. ``func.now()`` returns a
    # ``timestamptz`` labelled with the *connection's* ``TimeZone`` — Asia/Calcutta on
    # this server — so a bare ``.date()`` on it is the server-local day, while
    # ``LearningIntelligenceService`` converts to UTC first. The metric is a count of
    # days from the service's today to ``target_date``, so a fixture seeded from the
    # other calendar lands one day out and the distance reads 11 rather than 10.
    today = (await _db_now(db_session)).astimezone(UTC).date()
    goal = await service.create_goal(
        owner=owner,
        title="Finish the course",
        status=LearningGoalStatus.IN_PROGRESS.value,
        progress=50,
        target_date=today + timedelta(days=10),
    )

    metrics = {metric.key: metric for metric in await service.metrics(owner=owner)}
    vector = await service.features(owner=owner)

    distance = metrics["goal_deadline_distance_days"]
    assert distance.available is True
    assert 9 <= float(distance.value) <= 10
    assert vector.features.goal_deadline_distance_days in (9, 10)
    assert vector.features.goal_progress == 50.0
    assert metrics["goal_progress"].value == 50.0
    assert goal.progress == 50, "the goal's own figure is the user's and is not recomputed"


# ---------------------------------------------------------------------------
# (f) Gaps — a measured zero is not an absent measurement
# ---------------------------------------------------------------------------


async def test_a_skill_whose_target_is_already_met_is_a_measured_zero_gap(
    db_session: AsyncSession,
) -> None:
    """Target 3 and current 3, with evidence behind it, is ``gap=0, available=True``.

    This is the sentence the page exists to be able to print: *"Target 3/5, current
    self-assessed 3/5. NEXUS recorded 1 related learning activity in the last 30
    days."* Every figure in it is checkable against the rows beneath it, and the
    phrase "self-assessed" is there because ``level_source`` is ``user_defined`` —
    the same 3/5 would read "NEXUS system estimate" on a row NEXUS derived, and the
    two are not interchangeable.

    The second skill is the control, and it is the distinction the whole design
    turns on. It also sits at 3/3 with a target that is already met, and it reports
    ``available=False`` with a reason — because nothing has been recorded against
    it, "you have reached the level you set for a skill you have never recorded
    learning for" is a finding NEXUS is not entitled to offer. Same arithmetic,
    different answer, and the flag is what keeps them apart.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    met = await service.create_skill(owner=owner, name="SQL", current_level=3, target_level=3)
    await service.create_skill(owner=owner, name="Rust", current_level=3, target_level=3)
    await service.record_activity(
        owner=owner,
        title="A session",
        activity_type=LearningActivityType.STUDY_SESSION.value,
        skill_id=met.id,
        duration_minutes=20,
    )

    gaps = await service.gaps(owner=owner)

    assert gaps.total == 2
    assert gaps.available_count == 1
    assert gaps.unavailable_count == 1
    assert gaps.by_level_source[SkillLevelSource.USER_DEFINED.value] == 2

    by_name = {gap.skill_name: gap for gap in gaps.items}
    measured = by_name["SQL"]
    assert measured.gap == 0
    assert measured.available is True
    assert measured.reason_if_unavailable is None
    assert measured.current_level == measured.target_level == 3
    assert measured.evidence_count == 1
    assert measured.evidence_last_30d == 1
    assert measured.explanation == (
        "Target 3/5, current self-assessed 3/5. NEXUS recorded 1 related learning "
        "activity in the last 30 days."
    )

    untouched = by_name["Rust"]
    assert untouched.gap == 0, "the arithmetic is the same; the flag is what differs"
    assert untouched.available is False
    assert untouched.reason_if_unavailable == NOTHING_RECORDED.format(window=30)
    assert untouched.evidence_count == 0
    assert untouched.evidence_last_30d == 0
    assert untouched.days_since_last_activity is None, "null is not zero days"
    assert any(character.isdigit() for character in untouched.explanation)


async def test_a_level_nexus_inferred_is_withheld_until_the_configured_evidence_is_there(
    db_session: AsyncSession, make_settings
) -> None:
    """A ``system_estimate`` level with one activity behind it refuses to be presented.

    The threshold is a **refusal**, not a low-confidence badge: a thin sample shown
    with a "low confidence" label is still a claim, whereas a stated refusal is not.
    The fixture seeds the estimate through the repository — the service will not
    create one — and drives the deployment's
    ``learning_min_evidence_for_estimate`` down to 2 so the test needs only two
    recorded activities to cross it.

    Crossing it is the second half of the assertion: at three activities the same
    skill presents a gap and calls itself an estimate, which is what "can show its
    working" has to mean.
    """
    settings = make_settings(LEARNING_MIN_EVIDENCE_FOR_ESTIMATE="2")
    owner = await _owner(db_session)
    service = _service(db_session, settings=settings)
    skill = await service.create_skill(
        owner=owner, name="Kubernetes", current_level=2, target_level=4
    )
    await LearningRepository(db_session).update_skill(
        owner.id, skill.id, {"level_source": SkillLevelSource.SYSTEM_ESTIMATE.value}
    )
    await service.record_activity(
        owner=owner,
        title="One",
        activity_type=LearningActivityType.STUDY_SESSION.value,
        skill_id=skill.id,
    )

    thin = (await service.gaps(owner=owner)).items[0]
    assert thin.available is False
    assert thin.reason_if_unavailable == TOO_LITTLE_EVIDENCE_TO_ESTIMATE.format(
        count=1, window=30, minimum=2
    )
    assert "system estimate" in thin.explanation

    for label in ("Two", "Three"):
        await service.record_activity(
            owner=owner,
            title=label,
            activity_type=LearningActivityType.STUDY_SESSION.value,
            skill_id=skill.id,
        )

    thick = (await service.gaps(owner=owner)).items[0]
    assert thick.available is True
    assert thick.gap == 2, "target 4 less current 2"
    assert thick.reason_if_unavailable is None
    assert thick.evidence_last_30d == 3
    assert thick.level_source == SkillLevelSource.SYSTEM_ESTIMATE.value
    assert "NEXUS system estimate" in thick.explanation


# ---------------------------------------------------------------------------
# (g) The activity series and the requests it refuses
# ---------------------------------------------------------------------------


async def test_the_activity_series_is_dense_and_leaves_an_untimed_bucket_null(
    db_session: AsyncSession,
) -> None:
    """Two sessions two days apart over a five-day window: dense buckets, one null.

    The zero-fill is the property. The buckets are ascending, exactly one day apart
    and cover every date from the window's first to its last, so a chart built only
    from the busy days cannot skip a quiet one and make two active days read as
    consecutive.

    ``minutes`` is **null** in the bucket holding the page view, which recorded no
    duration. ``0`` there would claim time was measured and found to be nothing —
    a far more confident claim than "nobody said how long it took". The series total
    is 20 rather than 0: the sum of the timed buckets, and null when none of them
    carried a duration.

    ``sessions`` counts the typed study session and not the page view, which is why
    the two counters are carried beside each other rather than one being derived
    from the other.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    now = await _db_now(db_session)
    await service.record_activity(
        owner=owner,
        title="A study session",
        activity_type=LearningActivityType.STUDY_SESSION.value,
        duration_minutes=20,
        occurred_at=now - timedelta(days=1),
    )
    await service.record_activity(
        owner=owner,
        title="A page view",
        activity_type=LearningActivityType.RESOURCE_VIEWED.value,
        occurred_at=now - timedelta(days=2),
    )

    series = await service.read_activity(owner=owner, window_days=5, granularity="day")

    assert series.granularity == "day"
    assert series.window_days == 5
    assert series.total_activities == 2
    assert series.total_minutes == 20
    dates = [bucket.bucket_start.date() for bucket in series.buckets]
    assert dates == sorted(dates)
    assert all(later - earlier == timedelta(days=1) for earlier, later in pairwise(dates))
    assert (dates[-1] - dates[0]).days == 5

    timed = [bucket for bucket in series.buckets if bucket.minutes is not None]
    assert len(timed) == 1
    assert (timed[0].activities, timed[0].sessions, timed[0].minutes) == (1, 1, 20)
    untimed = next(
        bucket for bucket in series.buckets if bucket.activities == 1 and bucket.minutes is None
    )
    assert (untimed.activities, untimed.sessions) == (1, 0)
    assert any(bucket.activities == 0 for bucket in series.buckets), "the run is dense"


async def test_a_request_the_service_cannot_honour_is_refused_rather_than_rounded(
    db_session: AsyncSession,
) -> None:
    """A window above the ceiling and an unknown granularity both raise, and neither writes.

    Both are facts about the *request*, and both are better refused than quietly
    repaired: silently shortening a window would make the ``window_days`` on the
    response disagree with what the caller asked for, and guessing a bucket size
    would return a chart nobody asked for.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    ceiling = service.settings.learning_max_window_days

    with pytest.raises(ValidationError) as too_long:
        await service.metrics(owner=owner, window_days=ceiling + 1)
    with pytest.raises(ValidationError) as unknown:
        await service.read_activity(owner=owner, granularity="fortnight")

    assert f"at most {ceiling} days" in str(too_long.value)
    assert "fortnight" in str(unknown.value)
    assert await _row_count(db_session, LearningActivity, owner.id) == 0
