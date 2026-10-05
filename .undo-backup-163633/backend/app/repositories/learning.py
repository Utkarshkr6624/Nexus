"""Persistence for Phase 9: learning goals, skills, and the activities between them.

The repository owns SQL and nothing else. It never raises a *domain* error: a
goal, skill or activity belonging to another account is answered with ``None``,
``False`` or an empty page, never an exception, because the route above turns
``None`` into a 404 and this layer has no opinion about what a 404 means. The
refusals it does carry are about the *caller's* code and not about anybody's
data — an unknown ``status``, an ``activity_type`` outside the vocabulary, a
level off the 1-5 scale — and all of them raise :class:`ValueError`, which is the
same bargain :mod:`app.repositories.developer` strikes.

**Every read filters on ``user_id`` in the ``WHERE`` clause**, never by
filtering a loaded page afterwards. A row the caller may not see is never loaded
at all, and another account's skill is answered exactly as an id nobody ever
issued — identically, which is what keeps these endpoints from becoming an
existence oracle for other people's goal ids.

Four decisions carry the file, and all four exist because of the same rule:
*NEXUS does not know how good anyone is at anything.*

**Levels are the user's, and the write set says so.** :attr:`Skill.current_level`
and :attr:`Skill.target_level` are numbers somebody typed, so they are ordinary
editable columns here. :attr:`Skill.level_source` is the provenance and travels
with every write that moves a level, because a level that reached a screen
without a stated provenance is exactly the failure this phase exists to prevent.
:attr:`Skill.evidence_count` and :attr:`Skill.last_activity_at` are **not**
editable: they are NEXUS's own observations of what was recorded against the
skill, and if a PATCH could move them, a skill could claim six recorded study
sessions that do not exist and the gap service would quote them as evidence.
:meth:`LearningRepository.record_skill_evidence` is the only writer, which is
also why it exists as a separate method rather than as a key in the edit
mapping.

**The evidence bump never rewinds.** :meth:`record_skill_evidence` moves
``last_activity_at`` to ``greatest(last_activity_at, occurred_at)``, not to the
instant it was handed. A user back-filling last week's study log would
otherwise be able to make their most recent activity older than one recorded
today, and the staleness rule that reads this column would fire against a skill
that has been worked on since.

**A gap is never stored, so nothing here computes one.** No column on this
schema holds "how far from target", and no method above invents one: what the
gap service needs is :meth:`activity_counts_by_skill`, which is the *count of
recorded rows inside a window* and not a judgement about them. The count for a
skill with nothing in the window is a real ``0`` — the window was looked at and
was empty — and the caller is expected to keep it distinct from a skill that was
never measured at all.

**Counted and summed are different columns, and one of them is nullable.**
:meth:`activity_totals` returns ``activities`` coalesced to ``0`` because a
count of rows is always computable, and ``measured_minutes`` as ``None`` when no
activity in the window carried a duration. ``0`` minutes would claim a measured
zero-length window, when the truth is that nobody recorded a span. ``duration_
minutes`` is null for an *event* ("I finished the chapter") rather than for a
*span* ("I spent forty minutes on it"), and the total preserves that distinction
instead of averaging it away.

Smaller rules, inherited from Phase 8 rather than invented here. ``updated_at``
is written explicitly into every ``UPDATE``, because
:class:`~app.db.base.TimestampMixin`'s ``onupdate`` is something SQLAlchemy
applies to statements it generates itself. A naive ``datetime`` handed to a
``timestamptz`` column is read as UTC, because PostgreSQL would otherwise
interpret it in whatever ``TimeZone`` the connection was configured with.
:class:`~app.models.learning.LearningActivity` has no ``updated_at`` at all —
it is append-only — so the activity methods are the one place where the explicit
stamp does not appear.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    LearningActivityType,
    LearningGoalStatus,
    SkillLevelSource,
    validate_learning_activity_type,
    validate_learning_goal_status,
    validate_project_priority,
    validate_skill_level_source,
)
from app.models.learning import (
    DEFAULT_SKILL_LEVEL_SOURCE,
    MAX_SKILL_LEVEL,
    MIN_SKILL_LEVEL,
    LearningActivity,
    LearningGoal,
    Skill,
)
from app.repositories.analytics import utc_day

__all__ = ["ActivityTotals", "LearningRepository"]

#: Page size ceiling. The request layer validates ``?limit=`` and this is the
#: second lock on the same door rather than a second policy: a learning history
#: is a paginated list of rows, not a report.
_MAX_PAGE_SIZE = 200
#: What a caller that says nothing gets. Small on purpose — the dashboard's
#: goal list is a sidebar, not a table.
_DEFAULT_PAGE_SIZE = 50
#: Ceiling for the windowed totals. Bounded for the same reason as the page
#: size and larger because it is served to a metrics window rather than to a
#: paginated list.
_MAX_WINDOW_ROWS = 20_000

#: The only columns a goal PATCH may write. ``completed_at`` is excluded on
#: purpose: it is a completion stamp, and only
#: :meth:`LearningRepository.complete_goal` may write one. ``user_id`` and
#: ``created_at`` are absent for the reason they are absent everywhere.
_EDITABLE_GOAL_COLUMNS: frozenset[str] = frozenset(
    {
        "description",
        "estimated_effort_minutes",
        "note_id",
        "priority",
        "progress",
        "project_id",
        "status",
        "target_date",
        "target_skill_id",
        "target_topic",
        "title",
    }
)

#: The only columns a skill PATCH may write. ``evidence_count`` and
#: ``last_activity_at`` are absent because they are observations rather than
#: declarations: see the module docstring.
_EDITABLE_SKILL_COLUMNS: frozenset[str] = frozenset(
    {
        "category",
        "confidence",
        "current_level",
        "description",
        "level_source",
        "name",
        "target_level",
    }
)

#: Every column a skill create may write. Kept separate from the edit set so a
#: new skill and an amended one are two different permissions rather than one
#: mapping reused by accident.
_CREATABLE_SKILL_COLUMNS: frozenset[str] = _EDITABLE_SKILL_COLUMNS

#: Per-column validators applied to every goal and skill write, keyed by column
#: name. Anything a ``validate_*`` helper exists for is checked here rather than
#: in each of the four write paths, so a vocabulary that grows is checked in one
#: place instead of three.
_GOAL_VALIDATORS: Mapping[str, Callable[[Any], Any]] = {
    "priority": validate_project_priority,
    "status": validate_learning_goal_status,
}
_SKILL_VALIDATORS: Mapping[str, Callable[[Any], Any]] = {
    "level_source": validate_skill_level_source,
}

#: Bounded ranges for the columns the schema enforces with a check constraint
#: but this layer wants to report by name. The constraint is the guarantee; this
#: is the version of it that says *which* value was wrong.
_MAX_CONFIDENCE = 100
_MIN_CONFIDENCE = 0
_MAX_PROGRESS = 100
_MIN_PROGRESS = 0


@dataclass(frozen=True, slots=True)
class ActivityTotals:
    """Everything the learning window needs, in one statement.

    ``measured_minutes`` is ``None`` when no activity in the window carried a
    duration at all, which is a different fact from a window whose activities
    were all zero-length and is the one the phase's "a figure that could not be
    computed is null, never 0" rule exists to preserve. ``activities`` is
    coalesced, because a count of rows is always computable — an empty window
    really does hold no activities.

    ``active_days`` counts distinct **UTC** calendar days carrying at least one
    recorded activity. It is a count of days something was recorded on. It is not
    a count of days anyone studied.
    """

    #: Recorded activities in the window. Zero for an empty window, which is a
    #: measurement rather than an absence.
    activities: int
    #: Summed ``duration_minutes``, or ``None`` when nothing in the window
    #: recorded a span.
    measured_minutes: int | None
    #: Distinct UTC days carrying at least one activity.
    active_days: int
    first_occurred_at: datetime | None
    last_occurred_at: datetime | None


def _as_utc(instant: datetime | None) -> datetime | None:
    """Read a caller-supplied instant as UTC when it carries no offset.

    A naive ``datetime`` handed to a ``timestamptz`` column is interpreted in
    the *session's* ``TimeZone`` by PostgreSQL, so the same ``occurred_at`` would
    land at a different instant depending on which connection ran the insert —
    and an activity at 23:30 UTC could then be counted on the previous day by
    the very window this repository computes. An already-aware value passes
    through untouched.
    """
    if instant is None or instant.tzinfo is not None:
        return instant
    return instant.replace(tzinfo=UTC)


def _bounded(limit: int, offset: int) -> tuple[int, int]:
    """Clamp a page request to something a paginated list can serve.

    Both ends are clamped rather than rejected. The request layer is where
    ``?limit=500`` is a 422, and this is the belt to those braces: a caller that
    bypasses it still cannot ask for an unbounded slice. ``limit`` is floored at
    one because ``LIMIT 0`` returns nothing and a list route answering with an
    empty page for every request would look like an outage.
    """
    return max(1, min(int(limit), _MAX_PAGE_SIZE)), max(0, int(offset))


def _reject_unknown_columns(
    values: Mapping[str, Any], allowed: frozenset[str], *, what: str
) -> None:
    """Refuse a write to a column outside the set the caller was given.

    Raises:
        ValueError: If ``values`` names a column outside ``allowed``. Every such
            set is the *definition* of what the corresponding write may touch, so
            a key outside it is a programming error, and reporting it here beats
            a silent no-op or an ``IntegrityError`` from storage with no mention
            of which field was wrong.
    """
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"{what} may only write {', '.join(sorted(allowed))}; got {unknown}.")


#: Columns whose value is a **name** rather than a free note, and which are
#: therefore trimmed before anything else looks at them. ``"  FastAPI  "`` and
#: ``FastAPI`` are one name typed twice; only the trimmed form collides with the
#: row already in the table, so the trimming has to happen before the duplicate
#: lookup rather than after the write.
_TRIMMED_NAME_COLUMNS = frozenset({"name", "title"})


def _validated_writes(
    values: Mapping[str, Any],
    allowed: frozenset[str],
    validators: Mapping[str, Callable[[Any], Any]],
    *,
    what: str,
    utc_columns: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Normalise one write mapping: scope, vocabulary, ranges and UTC.

    Every write path in this module funnels through here, which is the only way
    to guarantee that a goal edited through one method is checked exactly as a
    goal created through another. Unknown keys raise, values with a closed
    vocabulary are coerced through the ``validate_*`` helpers, the bounded
    integers are checked against the same ranges the check constraints enforce,
    and naive instants are attached to UTC.

    Args:
        values: Column name to new value, as the caller supplied it.
        allowed: The columns this write may touch.
        validators: Per-column coercion. A column absent from the mapping is
            passed through unvalidated, which is the correct treatment for a
            column with an open vocabulary such as ``category``.
        what: Human name of the write, used in every message.
        utc_columns: Columns whose ``datetime`` value is read as UTC.

    Returns:
        A new mapping carrying validated values, ready to be splatted into
        ``UPDATE ... VALUES``.

    Raises:
        ValueError: If the mapping is empty, names a column outside ``allowed``,
            carries a value the vocabulary does not have, trims a name down to
            nothing, or puts a bounded integer outside its range.
    """
    if not values:
        raise ValueError(f"{what} must change at least one field.")
    _reject_unknown_columns(values, allowed, what=what)

    writes: dict[str, Any] = {}
    for key, value in values.items():
        validator = validators.get(key)
        if validator is not None:
            value = validator(value).value
        if key in utc_columns:
            value = _as_utc(value)
        if key in _TRIMMED_NAME_COLUMNS and isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError(
                    f"{what} cannot set {key} to whitespace; a {key} that renders as "
                    "nothing is not a name."
                )
        writes[key] = value

    for name, value, low, high in (
        ("progress", writes.get("progress"), _MIN_PROGRESS, _MAX_PROGRESS),
        ("current_level", writes.get("current_level"), MIN_SKILL_LEVEL, MAX_SKILL_LEVEL),
        ("target_level", writes.get("target_level"), MIN_SKILL_LEVEL, MAX_SKILL_LEVEL),
        ("confidence", writes.get("confidence"), _MIN_CONFIDENCE, _MAX_CONFIDENCE),
    ):
        if value is not None and not low <= int(value) <= high:
            raise ValueError(f"{what} cannot set {name} to {value!r}; expected {low}..{high}.")
    return writes


def _activity_filters(
    owner_id: uuid.UUID,
    *,
    skill_id: uuid.UUID | None,
    goal_id: uuid.UUID | None,
    activity_type: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list[Any]:
    """Build the one ``WHERE`` clause the activity reads share.

    The list, the totals and the per-skill counts all describe the same set —
    "which activities does this window match" — and a filter written out three
    times is three chances for the evidence count under a skill to disagree with
    the history printed beside it.

    The window is half-open: ``since`` is inclusive and ``until`` exclusive. A
    closed window would double-count an activity landing exactly on the boundary
    between two adjacent windows, which for a day-bucketed chart is the boundary
    that matters most.

    Args:
        owner_id: Whose activities, always asserted rather than filtered after.
        skill_id: Narrow to one skill, or ``None`` for all of them. A null
            ``skill_id`` on a stored activity is excluded by this filter rather
            than folded into it.
        goal_id: Narrow to one goal.
        activity_type: Narrow to one :class:`~app.models.enums.LearningActivityType`
            member.
        since: Inclusive lower bound on ``occurred_at``.
        until: Exclusive upper bound on ``occurred_at``.

    Returns:
        The predicates, owner first, so ``ix_learning_activities_user_occurred``
        serves the owner-and-window probe.
    """
    filters: list[Any] = [LearningActivity.user_id == owner_id]
    if skill_id is not None:
        filters.append(LearningActivity.skill_id == skill_id)
    if goal_id is not None:
        filters.append(LearningActivity.goal_id == goal_id)
    if activity_type is not None:
        filters.append(LearningActivity.activity_type == activity_type)
    if since is not None:
        filters.append(LearningActivity.occurred_at >= _as_utc(since))
    if until is not None:
        filters.append(LearningActivity.occurred_at < _as_utc(until))
    return filters


class LearningRepository:
    """Persistence for Phase 9: goals, skills and the activities that join them.

    Every read is owner-scoped and every write carries the owner id explicitly,
    so a caller cannot adopt another account's row by passing a mismatched pair.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------

    async def count_goals(
        self, owner_id: uuid.UUID, *, status: LearningGoalStatus | str | None = None
    ) -> int:
        """How many goals this owner has, optionally in one state.

        The check behind ``learning_max_goals``, which has to be answerable
        *before* the insert rather than by counting rows afterwards and
        reporting a confusing conflict.

        Args:
            owner_id: Whose goals to count.
            status: Count only goals in this state. ``None`` counts every goal,
                archived ones included — an archived goal the user wants to keep
                still occupies its slot, and a cap that silently ignored it
                would be a number that could rise without anything being created.

        Returns:
            The count, zero for an owner with no goals.
        """
        filters: list[Any] = [LearningGoal.user_id == owner_id]
        if status is not None:
            filters.append(LearningGoal.status == validate_learning_goal_status(status).value)
        return int(
            await self.session.scalar(
                select(func.count()).select_from(LearningGoal).where(*filters)
            )
        )

    async def list_goals(
        self,
        owner_id: uuid.UUID,
        *,
        status: LearningGoalStatus | str | None = None,
        target_skill_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        target_after: date | None = None,
        target_before: date | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[LearningGoal], int]:
        """This owner's goals and the total the filters match.

        Ordered by ``target_date`` ascending with the undated goals last,
        because a goal list is a plan and a plan is ordered by what is due.
        ``created_at`` then ``id`` break the ties, so paging is stable: two goals
        may share a date exactly, and without a total order a caller paging
        through would see one twice and another on no page.

        The page and its total come from one statement — a window count over the
        same rows — so the header and the list cannot describe two snapshots.
        The empty-page case has no row to carry the window count and is counted
        separately, re-asserting the owner predicate rather than trusting the
        caller's.

        The date window is half-open, matching the activity window: ``target_
        after`` is inclusive and ``target_before`` exclusive, and a goal with no
        ``target_date`` is excluded by either bound rather than being treated as
        due.

        Args:
            owner_id: Whose goals to list.
            status: Narrow to one state, or ``None`` for all of them.
            target_skill_id: Narrow to the goals aimed at one skill. Goals that
                name a topic before the skill exists carry a null
                ``target_skill_id`` and are excluded by this filter.
            project_id: Narrow to the goals linked to one project.
            target_after: Inclusive lower bound on ``target_date``.
            target_before: Exclusive upper bound on ``target_date``.
            limit: Page size, clamped to :data:`_MAX_PAGE_SIZE`.
            offset: Rows to skip, floored at zero.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        page_size, skip = _bounded(limit, offset)
        filters: list[Any] = [LearningGoal.user_id == owner_id]
        if status is not None:
            filters.append(LearningGoal.status == validate_learning_goal_status(status).value)
        if target_skill_id is not None:
            filters.append(LearningGoal.target_skill_id == target_skill_id)
        if project_id is not None:
            filters.append(LearningGoal.project_id == project_id)
        if target_after is not None:
            filters.append(LearningGoal.target_date >= target_after)
        if target_before is not None:
            filters.append(LearningGoal.target_date < target_before)

        statement = (
            select(LearningGoal, func.count().over().label("total"))
            .where(*filters)
            .order_by(
                LearningGoal.target_date.asc().nullslast(),
                LearningGoal.created_at.desc(),
                LearningGoal.id.desc(),
            )
            .limit(page_size)
            .offset(skip)
        )
        rows = list((await self.session.execute(statement)).all())
        if rows:
            return [row[0] for row in rows], int(rows[0].total)
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(LearningGoal).where(*filters)
            )
        )
        return [], total

    async def get_goal(self, owner_id: uuid.UUID, goal_id: uuid.UUID) -> LearningGoal | None:
        """One goal, or ``None`` if it is not this owner's.

        The route turns ``None`` into a 404, and that is deliberate rather than
        incidental: a foreign id is *not* a 403, because a 403 would confirm the
        id exists and turn the endpoint into a probe for which goal ids are
        real. Nothing is raised here and nothing about another account's row is
        loaded.
        """
        result = await self.session.execute(
            select(LearningGoal).where(
                LearningGoal.id == goal_id,
                LearningGoal.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_goal(
        self,
        owner_id: uuid.UUID,
        *,
        title: str,
        description: str | None = None,
        target_skill_id: uuid.UUID | None = None,
        target_topic: str | None = None,
        target_date: date | None = None,
        priority: str | None = None,
        status: LearningGoalStatus | str | None = None,
        progress: int = 0,
        estimated_effort_minutes: int | None = None,
        project_id: uuid.UUID | None = None,
        note_id: uuid.UUID | None = None,
    ) -> LearningGoal:
        """Record one thing the user said they meant to learn.

        Every default here is the schema's own, and the row is built with only
        the user-declared columns — so a goal arrives at ``not_started`` with
        ``progress`` 0, which is a statement about a goal that has demonstrably
        not started rather than a claim that nothing has happened.

        ``target_skill_id`` and ``target_topic`` are both optional and both may
        be set. A goal may name a skill that does not exist yet as free text, or
        a tracked skill, or both, because forcing a skill row first would put a
        barrier in front of "I want to learn Rust".

        ``completed_at`` is **not** writable here. A goal is created unfinished;
        the completion stamp is :meth:`complete_goal`'s to write, which keeps
        "when did they finish this" a fact with one producer.

        Args:
            owner_id: Whose goal this is.
            title: What the user called it.
            description: Optional user note.
            target_skill_id: The skill this is about, when one exists.
            target_topic: The same idea in free text, for a goal whose topic has
                no skill row yet.
            target_date: When the user means to have got there. Their date.
            priority: A :class:`~app.models.enums.ProjectPriority` member.
            status: A :class:`~app.models.enums.LearningGoalStatus` member.
            progress: The user's own percentage, 0-100.
            estimated_effort_minutes: The user's own estimate of the work. Never
                derived: NEXUS does not know what a task takes.
            project_id: Optional project this goal works towards.
            note_id: Optional note this goal belongs to.

        Returns:
            The stored row, refreshed so its server defaults carry the database's
            answer rather than being unset on the object handed back.

        Raises:
            ValueError: If ``status``, ``priority`` or ``progress`` is outside
                its vocabulary or range.
        """
        writes = _validated_writes(
            {
                "description": description,
                "estimated_effort_minutes": estimated_effort_minutes,
                "note_id": note_id,
                "project_id": project_id,
                "progress": progress,
                "target_skill_id": target_skill_id,
                "target_topic": target_topic,
                "title": title,
            },
            _EDITABLE_GOAL_COLUMNS,
            _GOAL_VALIDATORS,
            what="A goal creation",
        )
        goal = LearningGoal(id=uuid.uuid4(), user_id=owner_id, **writes)
        if target_date is not None:
            goal.target_date = target_date
        if status is not None:
            goal.status = validate_learning_goal_status(status).value
        if priority is not None:
            goal.priority = validate_project_priority(priority).value
        self.session.add(goal)
        await self.session.commit()
        await self.session.refresh(goal)
        return goal

    async def update_goal(
        self, owner_id: uuid.UUID, goal_id: uuid.UUID, values: Mapping[str, Any]
    ) -> LearningGoal | None:
        """Edit a goal, and keep the completion stamp honest.

        One owner-scoped ``UPDATE ... RETURNING`` rather than a read followed by
        a write, so another account's row is never loaded, only *not* updated —
        and the returned ``None`` is what the route turns into a 404.

        Only :data:`_EDITABLE_GOAL_COLUMNS` is writable; ``completed_at`` is
        not, and a key mapped to ``None`` is written as SQL ``NULL``, which is
        how a target skill or a project link is cleared.

        Two columns are kept together here rather than left to the caller,
        because ``ck_learning_goals_completed_has_terminal_status`` would
        otherwise turn a legitimate PATCH into a constraint error. Moving a goal
        **out** of ``completed`` clears ``completed_at``, and moving it **into**
        ``completed`` stamps it with the database clock unless the caller
        supplied an instant. The alternative — two columns the caller has to
        remember to set together — is how a row ends up completed with no date
        or dated with no status.

        Args:
            owner_id: Whose goal this is.
            goal_id: The goal to edit.
            values: Column name to new value, from
                :data:`_EDITABLE_GOAL_COLUMNS`. ``target_date`` is accepted and
                read as a plain date; ``completed_at`` is not.

        Returns:
            The updated row, or ``None`` when no row matched — a foreign or
            unknown id, answered identically to a foreign one.

        Raises:
            ValueError: If ``values`` is empty, names a column outside the
                editable set, carries an unknown status or priority, or puts
                ``progress`` outside 0-100.
        """
        writes = _validated_writes(
            values, _EDITABLE_GOAL_COLUMNS, _GOAL_VALIDATORS, what="A goal edit"
        )
        if "status" in writes and writes["status"] == LearningGoalStatus.COMPLETED.value:
            writes.setdefault("completed_at", func.now())
        elif "status" in writes:
            writes["completed_at"] = None
        writes["updated_at"] = func.now()

        statement = (
            update(LearningGoal)
            .where(
                LearningGoal.id == goal_id,
                LearningGoal.user_id == owner_id,
            )
            .values(**writes)
            .returning(LearningGoal)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def complete_goal(
        self,
        owner_id: uuid.UUID,
        goal_id: uuid.UUID,
        *,
        completed_at: datetime | None = None,
    ) -> LearningGoal | None:
        """Mark one goal finished, in a single statement that keeps it legal.

        Completion writes ``status``, ``completed_at`` and ``progress``
        together, because
        ``ck_learning_goals_completed_has_terminal_status`` holds the first two
        as one fact and a caller doing it in two requests would have a window in
        which the row claimed neither or both. Progress goes to 100 because a
        finished goal reporting 40% is a contradiction a user cannot argue with;
        it is the one derived figure in this method and it is derived from the
        completion, not from anything about the person.

        ``completed_at`` defaults to the database clock, which is the right
        default for the same reason it is on the column: the request's own clock
        is not evidence of when the user finished.

        Idempotent in the sense that matters: completing an already-completed
        goal is not an error, it re-stamps the row the caller named.

        Args:
            owner_id: Whose goal this is.
            goal_id: The goal to complete.
            completed_at: When they finished. Defaults to the database clock.

        Returns:
            The updated row, or ``None`` when no row matched — a foreign or
            unknown id, which the route answers with a 404.
        """
        writes: dict[str, Any] = {
            "status": LearningGoalStatus.COMPLETED.value,
            "progress": _MAX_PROGRESS,
            "completed_at": func.now(),
            "updated_at": func.now(),
        }
        if completed_at is not None:
            writes["completed_at"] = _as_utc(completed_at)

        statement = (
            update(LearningGoal)
            .where(
                LearningGoal.id == goal_id,
                LearningGoal.user_id == owner_id,
            )
            .values(**writes)
            .returning(LearningGoal)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def delete_goal(self, owner_id: uuid.UUID, goal_id: uuid.UUID) -> bool:
        """Remove one goal.

        The activities recorded towards it are **not** removed: their ``goal_id``
        is ``ON DELETE SET NULL``, so a goal the user abandoned leaves the record
        that they once worked on it — which is exactly the history the skill's
        evidence count summarises. Deleting the intention must not delete the
        evidence.

        Owner-scoped, so a foreign id deletes nothing and reports the same
        ``False`` an unknown id does.

        Args:
            owner_id: Whose goal this is.
            goal_id: The goal to delete.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.
        """
        result = await self.session.execute(
            delete(LearningGoal).where(
                LearningGoal.id == goal_id,
                LearningGoal.user_id == owner_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def count_skills(self, owner_id: uuid.UUID) -> int:
        """How many skills this owner is tracking.

        The check behind ``learning_max_skills``. It counts every row, which is
        the only reading a cap can have: a cap that changed when a skill was
        renamed would be a number that could move without anything being added.

        Args:
            owner_id: Whose skills to count.

        Returns:
            The count, zero for an owner who has named no skills.
        """
        return int(
            await self.session.scalar(
                select(func.count()).select_from(Skill).where(Skill.user_id == owner_id)
            )
        )

    async def list_skills(
        self,
        owner_id: uuid.UUID,
        *,
        category: str | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[Skill], int]:
        """This owner's skills and the total the filters match.

        Ordered by ``name`` so the list is stable between requests, with ``id``
        as a tiebreaker: ``uq_skills_owner_name`` makes the pair unique per
        account already, and the ``id`` is here so that a future relaxation of
        that constraint cannot quietly start making paging lossy.

        Args:
            owner_id: Whose skills to list.
            category: Narrow to one category. The vocabulary is open —
                ``language``, ``framework``, ``domain`` and ``practice`` are
                suggestions, not a closed set — so this is an equality filter
                and not a validation. Skills with no category are excluded by it.
            limit: Page size, clamped to :data:`_MAX_PAGE_SIZE`.
            offset: Rows to skip, floored at zero.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        page_size, skip = _bounded(limit, offset)
        filters: list[Any] = [Skill.user_id == owner_id]
        if category is not None:
            filters.append(Skill.category == category)

        statement = (
            select(Skill, func.count().over().label("total"))
            .where(*filters)
            .order_by(Skill.name.asc(), Skill.id.asc())
            .limit(page_size)
            .offset(skip)
        )
        rows = list((await self.session.execute(statement)).all())
        if rows:
            return [row[0] for row in rows], int(rows[0].total)
        total = int(
            await self.session.scalar(select(func.count()).select_from(Skill).where(*filters))
        )
        return [], total

    async def get_skill(self, owner_id: uuid.UUID, skill_id: uuid.UUID) -> Skill | None:
        """One skill, or ``None`` if it is not this owner's.

        Owner-scoped like every other single-row read, so a foreign id and an
        unknown id are the same answer.
        """
        result = await self.session.execute(
            select(Skill).where(
                Skill.id == skill_id,
                Skill.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_skill_by_name(self, owner_id: uuid.UUID, name: str) -> Skill | None:
        """The skill this owner already tracks under ``name``.

        The lookup behind the ``uq_skills_owner_name`` conflict: the skill route
        wants to say "you are already tracking Python" in a sentence a person can
        act on, and an :class:`~sqlalchemy.exc.IntegrityError` carries a
        constraint name rather than a message.

        **Matched without regard to case, and after trimming.** The constraint is
        an exact-match btree, so ``rust`` and ``Rust`` are two legal rows — and
        two rows holding two levels that can disagree is precisely what
        ``uq_skills_owner_name`` exists to prevent, so the *service* refuses the
        second one before it reaches storage. This method is that check, and it is
        deliberately the looser of the two: it is the read that answers "has this
        person already named this thing", and a person who writes ``FastAPI`` and
        ``fastapi`` has named one thing twice.

        Scoped to the owner on purpose. Two accounts may each track "Python" —
        the constraint is per account — so a global lookup would report a
        conflict about a vocabulary they do not share.
        """
        result = await self.session.execute(
            select(Skill).where(
                Skill.user_id == owner_id,
                func.lower(Skill.name) == name.strip().lower(),
            )
        )
        return result.scalar_one_or_none()

    async def create_skill(
        self,
        owner_id: uuid.UUID,
        *,
        name: str,
        category: str | None = None,
        description: str | None = None,
        current_level: int | None = None,
        target_level: int | None = None,
        level_source: SkillLevelSource | str | None = None,
        confidence: int | None = None,
    ) -> Skill:
        """Name one thing the user is learning.

        A new skill is a name and nothing else, so the row arrives with the
        schema's honest starting position: ``current_level`` 1, ``target_level``
        3, ``level_source='user_defined'`` and ``confidence`` 0. Those are
        defaults rather than arguments, and ``confidence`` 0 means *nothing to
        estimate from* rather than "low but present" — which is why a skill
        created as ``system_estimate`` would be claiming an inference that has
        not happened yet, and why passing ``level_source`` here is a statement
        the caller has to actually make.

        The levels are the user's by construction and are never derived from the
        activities below: nothing in this method counts anything, so there is no
        path by which an activity silently raises somebody's level.

        Args:
            owner_id: Whose skill this is.
            name: The user's own name for it. Unique per account.
            category: A free word; the suggested categories are not a closed set.
            description: Optional user note.
            current_level: 1-5, defaulting to the schema's floor.
            target_level: 1-5, defaulting to the schema's three.
            level_source: Who is claiming the level.
            confidence: 0-100, how much evidence backs an estimate.

        Returns:
            The stored row, refreshed so its server defaults carry the database's
            answer.

        Raises:
            ValueError: If ``level_source`` is unknown, or a level or the
                confidence is outside its range.

        Raises:
            :class:`~sqlalchemy.exc.IntegrityError`: If this owner already has a
                skill at ``name``. Surfaced rather than swallowed, because the
                caller that wants the friendly wording asks
                :meth:`get_skill_by_name` first.
        """
        writes = _validated_writes(
            {
                "category": category,
                "confidence": confidence,
                "current_level": current_level,
                "description": description,
                "name": name,
                "target_level": target_level,
            },
            _CREATABLE_SKILL_COLUMNS,
            _SKILL_VALIDATORS,
            what="A skill creation",
        )
        if writes.get("current_level") is None:
            writes.pop("current_level", None)
        if writes.get("target_level") is None:
            writes.pop("target_level", None)
        if writes.get("confidence") is None:
            writes.pop("confidence", None)

        skill = Skill(
            id=uuid.uuid4(),
            user_id=owner_id,
            level_source=validate_skill_level_source(
                level_source if level_source is not None else DEFAULT_SKILL_LEVEL_SOURCE
            ).value,
            **writes,
        )
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return skill

    async def update_skill(
        self, owner_id: uuid.UUID, skill_id: uuid.UUID, values: Mapping[str, Any]
    ) -> Skill | None:
        """Edit a skill's claim, and nothing about its evidence.

        Only :data:`_EDITABLE_SKILL_COLUMNS` is writable. ``evidence_count`` and
        ``last_activity_at`` are excluded because they are *observations*: if a
        PATCH could move them, a skill could claim study sessions that were never
        recorded, and the gap service would then quote them as the evidence
        behind a level. :meth:`record_skill_evidence` is the only writer.

        A key mapped to ``None`` is written as SQL ``NULL``, which is how a
        category or a description is cleared.

        Args:
            owner_id: Whose skill this is.
            skill_id: The skill to edit.
            values: Column name to new value, from
                :data:`_EDITABLE_SKILL_COLUMNS`.

        Returns:
            The updated row, or ``None`` when no row matched — a foreign or
            unknown id, answered identically to a foreign one.

        Raises:
            ValueError: If ``values`` is empty, names a column outside the
                editable set, carries an unknown ``level_source``, or puts a
                level outside 1-5 or the confidence outside 0-100.
        """
        writes = _validated_writes(
            values, _EDITABLE_SKILL_COLUMNS, _SKILL_VALIDATORS, what="A skill edit"
        )
        writes["updated_at"] = func.now()
        statement = (
            update(Skill)
            .where(
                Skill.id == skill_id,
                Skill.user_id == owner_id,
            )
            .values(**writes)
            .returning(Skill)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def record_skill_evidence(
        self, owner_id: uuid.UUID, skill_id: uuid.UUID, occurred_at: datetime
    ) -> Skill | None:
        """Count one recorded activity against a skill and advance its recency.

        The only writer of ``evidence_count`` and ``last_activity_at``, and a
        single ``UPDATE`` rather than a read-modify-write so two activities
        recorded concurrently cannot both read the same count and lose one.

        ``last_activity_at`` moves to ``greatest(last_activity_at, occurred_at)``
        rather than to the instant handed in. A user back-filling last week's log
        would otherwise be able to make their most recent activity *older* than
        one recorded today, and the staleness rule that reads this column would
        fire against a skill that has been worked on since. The bump is a count
        of recorded rows and a clock; it is not a claim about the person.

        Args:
            owner_id: Whose skill this is.
            skill_id: The skill the activity was recorded against.
            occurred_at: When the activity happened, read as UTC when naive.

        Returns:
            The updated row, or ``None`` when no row matched — which for the
            recording service is the signal that the skill disappeared underneath
            it, and which must be reported rather than swallowed.
        """
        stamp = _as_utc(occurred_at)
        statement = (
            update(Skill)
            .where(
                Skill.id == skill_id,
                Skill.user_id == owner_id,
            )
            .values(
                evidence_count=Skill.evidence_count + 1,
                last_activity_at=func.greatest(func.coalesce(Skill.last_activity_at, stamp), stamp),
                updated_at=func.now(),
            )
            .returning(Skill)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def delete_skill(self, owner_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        """Remove one skill. Its activities are kept, not cascaded.

        The activity rows survive through the declared ``ON DELETE SET NULL`` on
        ``learning_activities.skill_id`` — migration ``0010`` changed that
        constraint from ``CASCADE`` precisely because a cascade destroys
        evidence: deleting one skill row silently removed every activity
        recorded against it, and ``learning_activities`` has no ``updated_at``
        to leave a trace. The activity outlives the thing it describes, as an
        append-only fact with an unattributed subject, which is a state the
        column is already ``NULL`` for. So the single ``DELETE`` is all there is
        here, and it is deliberately not a read-then-delete per child table.

        The user's levels go with it, and so does every ``career_evidence`` row
        that named it — those are ``SET NULL``, so the evidence survives as the
        user's own claim about a thing they did, with the pointer dropped.

        Owner-scoped, so a foreign id deletes nothing and reports the same
        ``False`` an unknown id does.

        Args:
            owner_id: Whose skill this is.
            skill_id: The skill to delete.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.
        """
        result = await self.session.execute(
            delete(Skill).where(
                Skill.id == skill_id,
                Skill.user_id == owner_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)

    # ------------------------------------------------------------------
    # Activities
    # ------------------------------------------------------------------

    async def list_activities(
        self,
        owner_id: uuid.UUID,
        *,
        skill_id: uuid.UUID | None = None,
        goal_id: uuid.UUID | None = None,
        activity_type: LearningActivityType | str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[LearningActivity], int]:
        """This owner's activities in a window, newest first, and the total.

        Ordered by ``occurred_at`` descending with ``id`` as a tiebreaker, so
        paging is stable: ``occurred_at`` is second-resolution and two
        activities can share it exactly, which would otherwise let a row appear
        on two pages and another on none.

        The filters are the ones :meth:`activity_totals` and
        :meth:`activity_counts_by_skill` take, built by the same
        :func:`_activity_filters` — a list describing a different set from the
        evidence count printed beside it is worse than no list at all.

        The window is half-open: ``since`` inclusive, ``until`` exclusive.

        Args:
            owner_id: Whose activities to list.
            skill_id: Narrow to one skill. Activities with no skill are excluded
                rather than folded into it.
            goal_id: Narrow to one goal.
            activity_type: Narrow to one
                :class:`~app.models.enums.LearningActivityType` member.
            since: Inclusive lower bound on ``occurred_at``.
            until: Exclusive upper bound on ``occurred_at``.
            limit: Page size, clamped to :data:`_MAX_PAGE_SIZE`.
            offset: Rows to skip, floored at zero.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        page_size, skip = _bounded(limit, offset)
        filters = _activity_filters(
            owner_id,
            skill_id=skill_id,
            goal_id=goal_id,
            activity_type=(
                None
                if activity_type is None
                else validate_learning_activity_type(activity_type).value
            ),
            since=since,
            until=until,
        )
        statement = (
            select(LearningActivity, func.count().over().label("total"))
            .where(*filters)
            .order_by(LearningActivity.occurred_at.desc(), LearningActivity.id.desc())
            .limit(page_size)
            .offset(skip)
        )
        rows = list((await self.session.execute(statement)).all())
        if rows:
            return [row[0] for row in rows], int(rows[0].total)
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(LearningActivity).where(*filters)
            )
        )
        return [], total

    async def create_activity(
        self,
        owner_id: uuid.UUID,
        *,
        title: str,
        activity_type: LearningActivityType | str,
        skill_id: uuid.UUID | None = None,
        goal_id: uuid.UUID | None = None,
        description: str | None = None,
        occurred_at: datetime | None = None,
        duration_minutes: int | None = None,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
    ) -> LearningActivity:
        """Record one thing the user did that counts as evidence.

        The row is append-only: this is the only write the table has, and there
        is no update method, because an activity is a fact about a moment and
        an ``updated_at`` stamp on one would assert the moment is still being
        revised.

        Recording the activity does **not** move the skill's ``evidence_count``.
        That is :meth:`record_skill_evidence`, a separate call, so that the
        counter has exactly one writer and a caller who records an activity
        against a skill that has since been deleted gets an honest ``None``
        rather than a silently absent count.

        ``duration_minutes`` stays ``None`` for an event — "I finished the
        chapter" is not "I spent zero minutes on it" — and ``occurred_at``
        defaults to the database clock, which is the only case where NEXUS
        supplies an instant.

        Args:
            owner_id: Whose activity this is.
            title: One line naming what was done.
            activity_type: A :class:`~app.models.enums.LearningActivityType`
                member. ``resource_viewed`` is the weakest of them and is
                weighted as such by whatever reads this.
            skill_id: The skill this is evidence for, when the user named one.
            goal_id: The goal this was recorded towards.
            description: Optional user note.
            occurred_at: When it happened, read as UTC when naive.
            duration_minutes: How long it took, or ``None`` for an event rather
                than a span.
            source_type: ``manual``, ``task``, ``note``, ``project`` or
                ``repository`` — the *label* on the pointer below, which is what
                stops "6 commits touched Python files" being read back as "6
                Python tasks completed".
            source_id: The record it was derived from.

        Returns:
            The stored row, refreshed so ``occurred_at`` and ``created_at`` carry
            the database's answer when the caller supplied neither.

        Raises:
            ValueError: If ``activity_type`` is outside the vocabulary, or
                ``duration_minutes`` is negative.
        """
        if duration_minutes is not None and int(duration_minutes) < 0:
            raise ValueError(
                f"An activity cannot record a negative duration: {duration_minutes!r}."
            )
        activity = LearningActivity(
            id=uuid.uuid4(),
            user_id=owner_id,
            activity_type=validate_learning_activity_type(activity_type).value,
            title=title,
            description=description,
            skill_id=skill_id,
            goal_id=goal_id,
            duration_minutes=duration_minutes,
            source_type=source_type,
            source_id=source_id,
        )
        if occurred_at is not None:
            activity.occurred_at = _as_utc(occurred_at)
        self.session.add(activity)
        await self.session.commit()
        await self.session.refresh(activity)
        return activity

    async def activity_totals(
        self,
        owner_id: uuid.UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> ActivityTotals:
        """Every window figure the learning metrics need, in one statement.

        One aggregate rather than four: each additional round trip is another
        chance for the card and the chart beside it to describe different
        windows, and this is the read behind the whole learning summary.

        ``measured_minutes`` is left uncoalesced on purpose. ``SUM`` over no rows
        — and over a window whose activities were all *events* with a null
        duration — is ``NULL``, and that null is the honest answer: nothing was
        timed. Coalescing it to ``0`` would claim a measured zero-length window.

        ``active_days`` counts distinct **UTC** calendar days carrying at least
        one recorded activity, bucketed through
        :func:`app.repositories.analytics.utc_day` so the answer is a property
        of the query rather than of whichever connection ran it.

        Args:
            owner_id: Whose activities to aggregate.
            since: Inclusive lower bound on ``occurred_at``.
            until: Exclusive upper bound on ``occurred_at``.

        Returns:
            The :class:`ActivityTotals` for the filtered set.
        """
        filters = _activity_filters(
            owner_id,
            skill_id=None,
            goal_id=None,
            activity_type=None,
            since=since,
            until=until,
        )
        statement = select(
            func.coalesce(func.count(LearningActivity.id), 0),
            func.sum(LearningActivity.duration_minutes),
            func.count(func.distinct(utc_day(LearningActivity.occurred_at))),
            func.min(LearningActivity.occurred_at),
            func.max(LearningActivity.occurred_at),
        ).where(*filters)
        row = (await self.session.execute(statement)).one()
        return ActivityTotals(
            activities=int(row[0]),
            measured_minutes=None if row[1] is None else int(row[1]),
            active_days=int(row[2]),
            first_occurred_at=row[3],
            last_occurred_at=row[4],
        )

    async def activity_counts_by_skill(
        self,
        owner_id: uuid.UUID,
        skill_ids: Sequence[uuid.UUID],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[uuid.UUID, int]:
        """How many activities each named skill recorded inside the window.

        The raw material for the gap computation, in **one** statement. The gap
        service needs a count per skill and the obvious alternative — a query per
        skill — is a round trip per row on a page that may show every skill the
        user has. A single grouped aggregate also means every skill on the page
        was counted by the same statement, so two skills cannot disagree about
        the window.

        Every requested id is present in the answer, **including the ones with
        no activity**, because a count of ``0`` over a window that was searched
        and found empty is a real measurement. The caller is expected to keep
        that distinct from a skill that was never measured at all, which is a
        question about the skill row rather than about this mapping — this method
        will happily report ``0`` for a skill it knows nothing about, and only
        the caller can tell that apart from an absence.

        Activities with no ``skill_id`` are not counted for anybody: "not
        recorded against a skill" is not evidence for every skill there is.

        Args:
            owner_id: Whose activities to count.
            skill_ids: The skills to count for. An empty sequence returns an
                empty mapping without touching the database, because ``GROUP BY
                ()`` is not a query anybody wants to send.
            since: Inclusive lower bound on ``occurred_at``.
            until: Exclusive upper bound on ``occurred_at``.

        Returns:
            A mapping covering exactly the ids passed in.
        """
        wanted = list(dict.fromkeys(skill_ids))
        if not wanted:
            return {}
        filters = _activity_filters(
            owner_id,
            skill_id=None,
            goal_id=None,
            activity_type=None,
            since=since,
            until=until,
        )
        filters.append(LearningActivity.skill_id.in_(wanted))
        result = await self.session.execute(
            select(LearningActivity.skill_id, func.count())
            .where(*filters)
            .group_by(LearningActivity.skill_id)
        )
        counted = {skill_id: int(count) for skill_id, count in result.all()}
        return {skill_id: counted.get(skill_id, 0) for skill_id in wanted}
