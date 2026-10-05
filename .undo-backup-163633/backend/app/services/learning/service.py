"""Phase 9 service: record what somebody meant to learn, and report it honestly.

What this module is for
-----------------------
:mod:`app.services.learning.metrics` knows how to turn recorded activities and
goals into eight numbers; :mod:`app.services.learning.gaps` knows how to turn a
skill and its activities into a distance from target. Neither knows about a
database, an ORM, an account or a clock. This module is the seam: it owns the
path from "this user typed an intention" to "here is a
:class:`~app.schemas.learning.LearningGoalRead`", and everything in between —
ownership, caps, validation, event emission, wire assembly and the three readings
the dashboard is built from.

The arithmetic is never re-implemented here
-------------------------------------------
:meth:`~LearningIntelligenceService.gaps` feeds real rows into
:func:`app.services.learning.gaps.skill_gaps` and maps the result onto the wire;
:meth:`~LearningIntelligenceService.metrics` feeds real rows into
:func:`app.services.learning.metrics.build_metrics`. Neither method contains a
``max(0, ...)``, a sum or a ratio of its own. That is not tidiness: the pure
modules enforce things a service cannot enforce about itself. A
:class:`~app.services.learning.gaps.SkillGap` **cannot be constructed** with an
explanation that omits the phrase its ``level_source`` requires, and a
:class:`~app.services.learning.metrics.LearningMetric` cannot be constructed with
a digitless explanation or one reaching for "mastery". Re-implementing the
arithmetic in the orchestration layer would move both guarantees down to a
convention somebody could quietly break, and the rule they protect — *a level is
the user's or visibly derived* — is the one this phase exists to hold.

What the service owns that the pure modules deliberately refuse to
------------------------------------------------------------------
**Time.** Every windowed read resolves its boundaries from the database clock
through :meth:`LearningIntelligenceService._now`, never from ``datetime.now()``.
Two application servers cannot then disagree about which day a session landed
on, and a gap computed "today" is the same gap on every machine.

**Ownership.** Every read filters on ``user_id`` inside the repository's
``WHERE`` clause, and this layer turns a miss into :class:`NotFoundError` —
never :class:`~app.core.exceptions.ForbiddenError`. Another account's goal,
skill or activity is answered with the *same message* as an id nobody has ever
issued, because two different answers would turn these endpoints into an
existence oracle.

**Caps.** ``learning_max_goals`` and ``learning_max_skills`` are checked by
counting rows *before* the insert. A cap enforced by the database instead would
surface as an ``IntegrityError`` or, worse, would not be enforced at all.

What the event feed is allowed to carry
--------------------------------------
**Ids, numbers and vocabulary strings. Never the user's own words.** A goal
title, a skill name and a skill description are the content of the row the event
points at; duplicating any of them into a feed nobody asked for is how a summary
view ends up quoting stale text — and the history feed is the least
access-controlled surface in the product. :meth:`_record_event` is the only
writer, and every metadata mapping below passes through it.

Absence is not zero, on either side of the wire
------------------------------------------------
The same rule the Phase 8 service holds, applied to learning. A metric that
could not be computed becomes ``value=None`` with ``available=False`` and a
reason; a window in which nothing was recorded is a *measured* zero and keeps
``value=0, available=True``. In the feature vector a figure that could not be
computed is ``null`` and never ``0`` — an account with no goals does not have
goals that are zero percent complete, and ``0`` there would be a claim about
somebody rather than about a record.

Why the history read is named ``read_activity``
-----------------------------------------------
``self.activity`` is the optional event sink, exactly as in
:class:`~app.services.developer.service.DeveloperIntelligenceService` and
:class:`~app.services.risk.detection.RiskDetectionService`. A method called
``activity`` would be shadowed by that attribute, and the read behind
``GET /learning/activity`` would silently return ``None``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.enums import (
    ActivityEvent,
    LearningActivityType,
    LearningGoalStatus,
    SkillLevelSource,
)
from app.models.learning import (
    DEFAULT_SKILL_CURRENT_LEVEL,
    DEFAULT_SKILL_TARGET_LEVEL,
    LearningActivity,
    LearningGoal,
    Skill,
)
from app.repositories.knowledge import NoteRepository
from app.repositories.learning import LearningRepository
from app.repositories.project import ProjectRepository
from app.schemas.learning import (
    LearningActivityBucketRead,
    LearningActivityListRead,
    LearningActivityRead,
    LearningActivitySeriesRead,
    LearningFeatureValues,
    LearningFeatureVectorRead,
    LearningGoalListRead,
    LearningGoalRead,
    LearningMetricRead,
    LearningSummaryRead,
    SkillGapListRead,
    SkillGapRead,
    SkillListRead,
    SkillRead,
)
from app.schemas.recommendation import band_count_sentence
from app.services.activity_service import ActivityService
from app.services.learning.gaps import SkillSample, skill_gaps
from app.services.learning.metrics import (
    ACTIVITY_GRANULARITIES,
    OPEN_GOAL_STATUSES,
    ActivityGranularity,
    ActivitySample,
    GoalSample,
    LearningMetric,
    MetricKey,
    activity_series,
    bucket_for,
    build_metrics,
)

if TYPE_CHECKING:
    from app.models.user import User

__all__ = ["LearningIntelligenceService"]

_T = TypeVar("_T")

#: The page size a caller who names none gets. Matches the repository's own
#: default so the service's fallback and the repository's are one number rather
#: than two that can drift.
_DEFAULT_PAGE_SIZE = 50
#: The page size used for the internal "read this owner's whole set" sweeps. It is
#: the repository's own ceiling, so one statement fetches as much as one can.
_READ_PAGE_SIZE = 200
#: How many recorded activities an account-wide sweep will read.
#:
#: :class:`~app.models.learning.LearningActivity` is append-only and has no
#: configured cap — unlike goals and skills, which ``learning_max_goals`` and
#: ``learning_max_skills`` bound — so this is the bound the sweeps put on it. The
#: rows are read **newest first**, which is what makes the truncation safe in the
#: direction that matters: every trailing-window figure (the seven-day and
#: thirty-day session counts, the minutes, the consistency rate, the whole
#: evidence window a gap reads) is exact for any account whose history fits, and a
#: longer history under-counts its distant past rather than its recent weeks.
_MAX_ACTIVITY_ROWS = 20_000
#: What ``GET /learning/activity`` buckets by when a caller names no granularity.
#: There is no ``learning_*_granularity_default`` setting because the three
#: values are the only ones the series builder accepts; making the default a
#: deployment knob would let an operator choose a bucket the chart cannot draw.
_DEFAULT_GRANULARITY = ActivityGranularity.DAY.value
#: The progress a completed goal is stamped with. The user's, restated by the
#: completion: a finished goal reporting 40% is a contradiction a user cannot
#: argue with, and it is derived from the completion rather than from anything
#: about the person.
_COMPLETED_PROGRESS = 100
#: The one activity type the series counts separately, because "6 sessions" and
#: "6 activities" answer different questions and a chart showing only the second
#: would overstate a fortnight of page views. Named from the enum rather than
#: re-spelled, so a member renamed upstream cannot leave a literal behind that
#: silently matches nothing.
_STUDY_SESSION = LearningActivityType.STUDY_SESSION.value

_GOAL_NOT_FOUND = "That learning goal was not found."
_SKILL_NOT_FOUND = "That skill was not found."
_PROJECT_NOT_FOUND = "That project was not found."
_NOTE_NOT_FOUND = "That note was not found."

#: The plural nouns the list headers count, and the band orders they read in. Both
#: are the schema's own vocabulary rather than this module's, and the sentences are
#: composed with :func:`app.schemas.recommendation.band_count_sentence` so a risk
#: list, a recommendation list and a learning list all register the same way: the
#: counts, and nothing that characterises what they show.
_GOAL_LIST_SUBJECT = "learning goals"
_ACTIVITY_LIST_SUBJECT = "learning activities"
_GOAL_STATUS_ORDER: tuple[str, ...] = tuple(status.value for status in LearningGoalStatus)
_ACTIVITY_TYPE_ORDER: tuple[str, ...] = tuple(kind.value for kind in LearningActivityType)


def _as_utc(value: datetime | None) -> datetime | None:
    """Read a naive instant as UTC; leave an aware one alone.

    The same rule :mod:`app.repositories.learning` applies before writing a
    ``timestamptz``. A session at 23:30 UTC would otherwise bucket onto the
    previous day on a connection configured for another zone, which moves
    ``learning_consistency`` and ``sessions_last_30d`` — figures this phase
    promises to be reproducible.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class LearningIntelligenceService:
    """Goals, skills and the recorded evidence between them, and what they say.

    One instance per request, built from repositories rather than from a session,
    exactly like :class:`~app.services.developer.service.DeveloperIntelligenceService`.
    It holds no state between calls, so two concurrent recordings against the
    same account are both correct: the evidence counter arbitrates through a
    single ``UPDATE`` in the repository rather than through anything this class
    remembers.
    """

    def __init__(
        self,
        repositories: LearningRepository,
        projects: ProjectRepository,
        notes: NoteRepository,
        activity: ActivityService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        Args:
            repositories: Phase 9 learning persistence. Everything read or written
                about goals, skills and activities goes through it, so every
                statement is owner-scoped in one place.
            projects: Project persistence, used for one thing: proving a
                ``project_id`` belongs to the caller before it is written onto a
                goal. Another account's project is *not found*, never forbidden.
            notes: Note persistence, for the same reason about a goal's
                ``note_id``. Required rather than optional because a goal that
                silently accepted a foreign note id would be the one write in
                this module that trusts an identifier from the client.
            activity: The history sink. ``None`` runs every lifecycle rule with
                nowhere to record them, which is a real mode for exercising the
                service against a hand-built snapshot and not the mode the API
                layer uses — see ``app.api.deps.get_learning_service``.
            settings: Resolved from the environment when not supplied. Supplies
                the goal and skill caps, the window bounds and the minimum
                evidence NEXUS needs before it will present an estimate it
                inferred itself.
        """
        self.repositories = repositories
        self.projects = projects
        self.notes = notes
        self.activity = activity
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------

    async def create_goal(
        self,
        *,
        owner: User,
        title: str,
        description: str | None = None,
        target_skill_id: uuid.UUID | None = None,
        target_topic: str | None = None,
        target_date: date | None = None,
        priority: str | None = None,
        status: str | None = None,
        progress: int = 0,
        estimated_effort_minutes: int | None = None,
        project_id: uuid.UUID | None = None,
        note_id: uuid.UUID | None = None,
    ) -> LearningGoalRead:
        """Record one thing the user said they meant to learn.

        **Every pointer is proved to belong to the caller before the write.** A
        ``target_skill_id``, ``project_id`` or ``note_id`` naming another
        account's row is a not-found, identically to an id nobody has issued —
        a 403 would confirm the id exists.

        ``progress`` and ``estimated_effort_minutes`` are the **user's** figures
        and are stored exactly as given. Nothing here sums activities into a
        progress bar: a study session and a goal are different units, and
        deriving one from the other is the first step towards NEXUS claiming to
        know whether somebody learned something.

        Args:
            owner: The caller, and the owner recorded on every row this writes.
            title: The user's own name for the goal.
            description: Optional note.
            target_skill_id: A skill in this account to aim at, or ``None`` when
                the topic has no skill row yet.
            target_topic: The subject in free text. Both may be set; neither has
                to be.
            target_date: The user's own deadline. NEXUS never supplies one.
            priority: A :class:`~app.models.enums.ProjectPriority` value.
            status: A :class:`~app.models.enums.LearningGoalStatus` value. Setting
                ``completed`` here does **not** stamp ``completed_at`` and does
                not emit the completion event; :meth:`complete_goal` owns both.
            progress: The user's own 0-100 figure.
            estimated_effort_minutes: The user's own estimate of the work.
            project_id: A project in this account the goal works towards.
            note_id: A note in this account the goal is filed under.

        Returns:
            The stored goal, with its server defaults filled in.

        Raises:
            ConflictError: If the account already holds ``learning_max_goals``
                goals. Archived goals count toward the cap, because deleting one
                to make room would delete the record the user kept it for.
            NotFoundError: If a supplied skill, project or note is not this
                account's.
            ValidationError: If the vocabulary or a bounded value is refused.
        """
        if await self.repositories.count_goals(owner.id) >= self.settings.learning_max_goals:
            raise ConflictError(
                f"This account already has the maximum of "
                f"{self.settings.learning_max_goals} learning goals."
            )
        await self._require_skill(owner=owner, skill_id=target_skill_id)
        await self._require_project(owner=owner, project_id=project_id)
        await self._require_note(owner=owner, note_id=note_id)

        try:
            goal = await self.repositories.create_goal(
                owner.id,
                title=title,
                description=description,
                target_skill_id=target_skill_id,
                target_topic=target_topic,
                target_date=target_date,
                priority=priority,
                status=status,
                progress=progress,
                estimated_effort_minutes=estimated_effort_minutes,
                project_id=project_id,
                note_id=note_id,
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error

        await self._record_event(
            ActivityEvent.LEARNING_GOAL_CREATED,
            owner=owner,
            project_id=project_id,
            metadata={
                "goal_id": str(goal.id),
                "status": goal.status,
                "priority": goal.priority,
            },
        )
        return LearningGoalRead.model_validate(goal)

    async def list_goals(
        self,
        *,
        owner: User,
        status: str | None = None,
        target_skill_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        target_after: date | None = None,
        target_before: date | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> LearningGoalListRead:
        """One page of this account's goals, with the state tally beside the rows.

        The tally is complete across **every** matching goal rather than across
        the page: a header that counted a page slice would report fewer completed
        goals on page two than on page one, and the count it printed would
        describe the slice rather than the set.

        Args:
            owner: The caller.
            status: Narrow to one :class:`~app.models.enums.LearningGoalStatus`.
            target_skill_id: Narrow to the goals aimed at one skill. Goals naming
                a topic before the skill exists carry no ``target_skill_id`` and
                are excluded rather than folded into it.
            project_id: Narrow to one project.
            target_after: Inclusive lower bound on ``target_date``.
            target_before: Exclusive upper bound. A goal with no date is
                excluded by either bound rather than being treated as due.
            limit: Rows the page may hold.
            offset: Matching rows to skip.

        Returns:
            The page, the total the filters match, and the complete tally.

        Raises:
            ValidationError: If ``status`` is outside the
                :class:`~app.models.enums.LearningGoalStatus` vocabulary. The
                repository is where the check lives — the same check the write
                path applies — and a ``ValueError`` escaping it would take the
                read down as a 500 over a query parameter rather than answering
                with the 422 the contract promises.
        """
        try:
            rows = await self._paged(
                lambda skip: self.repositories.list_goals(
                    owner.id,
                    status=status,
                    target_skill_id=target_skill_id,
                    project_id=project_id,
                    target_after=target_after,
                    target_before=target_before,
                    limit=_READ_PAGE_SIZE,
                    offset=skip,
                ),
                cap=self.settings.learning_max_goals,
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error
        skip = max(0, offset)
        by_status = {
            member.value: sum(1 for row in rows if row.status == member.value)
            for member in LearningGoalStatus
        }
        return LearningGoalListRead(
            items=[
                LearningGoalRead.model_validate(row) for row in rows[skip : skip + max(1, limit)]
            ],
            total=len(rows),
            limit=limit,
            offset=skip,
            by_status=by_status,
            summary=band_count_sentence(
                by_status, len(rows), _GOAL_LIST_SUBJECT, _GOAL_STATUS_ORDER
            ),
        )

    async def get_goal(self, *, owner: User, goal_id: uuid.UUID) -> LearningGoalRead:
        """One goal, or a 404 that does not confirm whose it was.

        Args:
            owner: The caller.
            goal_id: The goal to read.

        Returns:
            The goal row as the wire shape.

        Raises:
            NotFoundError: If no row with that id belongs to this account —
                identically to an id nobody has ever issued.
        """
        return LearningGoalRead.model_validate(await self._owned_goal(owner=owner, goal_id=goal_id))

    async def update_goal(
        self, *, owner: User, goal_id: uuid.UUID, values: Mapping[str, Any]
    ) -> LearningGoalRead:
        """Edit a goal the caller named only the fields they set.

        The route builds ``values`` with ``model_dump(exclude_unset=True)``,
        because ``None`` here means "write SQL NULL" and would otherwise clear a
        field the user never mentioned.

        ``completed_at`` is refused by the repository's write set, and that is the
        design rather than an omission: it is a completion stamp and
        :meth:`complete_goal` is the only producer, which is what keeps "when did
        they finish this" a fact with one writer.

        Args:
            owner: The caller.
            goal_id: The goal to edit.
            values: Column name to new value.

        Returns:
            The updated goal.

        Raises:
            ValidationError: If ``values`` is empty, names a column the goal may
                not write, or carries an unknown status, priority or an
                out-of-range ``progress``.
            NotFoundError: If the goal is not this account's, or a supplied
                skill, project or note belongs to somebody else.
        """
        if not values:
            raise ValidationError("A learning goal edit must change at least one field.")
        await self._require_skill(owner=owner, skill_id=values.get("target_skill_id"))
        await self._require_project(owner=owner, project_id=values.get("project_id"))
        await self._require_note(owner=owner, note_id=values.get("note_id"))

        try:
            goal = await self.repositories.update_goal(owner.id, goal_id, values)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        if goal is None:
            raise NotFoundError(_GOAL_NOT_FOUND)

        await self._record_event(
            ActivityEvent.LEARNING_GOAL_UPDATED,
            owner=owner,
            project_id=goal.project_id,
            metadata={"goal_id": str(goal.id), "status": goal.status},
        )
        return LearningGoalRead.model_validate(goal)

    async def complete_goal(
        self, *, owner: User, goal_id: uuid.UUID, completed_at: datetime | None = None
    ) -> LearningGoalRead:
        """Mark one goal finished: stamp it, set it to 100%, and record that.

        Three things happen in one method rather than three, because they are one
        fact. ``status`` becomes ``completed``, ``completed_at`` is stamped from
        the database clock unless the caller supplied an instant, and ``progress``
        becomes 100 — the database's check constraint holds the first two together
        and a caller doing them in separate requests would have a window in which
        the row claimed neither or both.

        Completing an already-completed goal is not an error; it re-stamps the row
        the caller named.

        Args:
            owner: The caller.
            goal_id: The goal to complete.
            completed_at: When they finished. Defaults to the database clock,
                which is the right default because the request's own clock is not
                evidence of when the user finished.

        Returns:
            The completed goal.

        Raises:
            NotFoundError: If the goal is not this account's.
        """
        goal = await self.repositories.complete_goal(owner.id, goal_id, completed_at=completed_at)
        if goal is None:
            raise NotFoundError(_GOAL_NOT_FOUND)

        await self._record_event(
            ActivityEvent.LEARNING_GOAL_COMPLETED,
            owner=owner,
            project_id=goal.project_id,
            metadata={
                "goal_id": str(goal.id),
                "progress": _COMPLETED_PROGRESS,
                "skill_id": str(goal.target_skill_id) if goal.target_skill_id else None,
            },
        )
        return LearningGoalRead.model_validate(goal)

    async def delete_goal(self, *, owner: User, goal_id: uuid.UUID) -> None:
        """Remove one goal, and keep the record that they once worked on it.

        The activities recorded towards it survive: their ``goal_id`` is
        ``ON DELETE SET NULL``. Deleting the intention must not delete the
        evidence — a skill's evidence count is a history, and a user who
        abandons a goal has not unlearned anything.

        Args:
            owner: The caller.
            goal_id: The goal to remove.

        Raises:
            NotFoundError: If no row with that id belongs to this account.
        """
        goal = await self._owned_goal(owner=owner, goal_id=goal_id)
        if not await self.repositories.delete_goal(owner.id, goal.id):
            raise NotFoundError(_GOAL_NOT_FOUND)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def create_skill(
        self,
        *,
        owner: User,
        name: str,
        category: str | None = None,
        description: str | None = None,
        current_level: int | None = None,
        target_level: int | None = None,
    ) -> SkillRead:
        """Name one thing the user is learning, at the level they say they are at.

        **A level sent by a client is the client's claim**, so the row is always
        written with ``level_source='user_defined'``. A caller cannot file its own
        inference as a self-assessment, and it cannot create a skill already
        carrying a ``system_estimate`` whose evidence has not been recorded yet —
        the one level in this schema NEXUS is allowed to derive, and the phase's
        read path decides when it has earned one.

        A duplicate name is a **conflict** rather than an integrity error, because
        ``uq_skills_owner_name`` would otherwise reach the client as a 500
        carrying a constraint name rather than a sentence a person can act on.

        Args:
            owner: The caller.
            name: The user's own name for it. Unique within this account.
            category: A free grouping word; the suggested four are not a closed
                set, so an unrecognised value is inert rather than invalid.
            description: Optional note.
            current_level: 1-5. Defaults to the schema's floor.
            target_level: 1-5. Defaults to three. NEXUS never raises it.

        Returns:
            The stored skill, with its defaults filled in.

        Raises:
            ConflictError: If this account already tracks that name, or has
                reached ``learning_max_skills``.
            ValidationError: If a level or the confidence is outside its range.
        """
        if await self.repositories.count_skills(owner.id) >= self.settings.learning_max_skills:
            raise ConflictError(
                f"This account already has the maximum of "
                f"{self.settings.learning_max_skills} skills."
            )
        if await self.repositories.get_skill_by_name(owner.id, name) is not None:
            raise ConflictError("That skill is already tracked for this account.")

        try:
            skill = await self.repositories.create_skill(
                owner.id,
                name=name,
                category=category,
                description=description,
                current_level=current_level,
                target_level=target_level,
                level_source=SkillLevelSource.USER_DEFINED,
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error
        await self._record_event(
            ActivityEvent.SKILL_CREATED,
            owner=owner,
            metadata={
                "skill_id": str(skill.id),
                "level_source": skill.level_source,
                "current_level": skill.current_level,
                "target_level": skill.target_level,
            },
        )
        return SkillRead.model_validate(skill)

    async def list_skills(
        self,
        *,
        owner: User,
        category: str | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> SkillListRead:
        """One page of this account's skills, with the tallies the header shows.

        ``by_level_source`` is the tally worth carrying beside the rows: it answers
        "how much of this page is NEXUS's opinion rather than the user's", which
        is a question the design is obliged to make answerable rather than to make
        go away. ``by_category`` is **not** completed, because the category
        vocabulary is deliberately open and zero-filling it would invent groupings
        nobody used.

        Args:
            owner: The caller.
            category: Narrow to one grouping word. An equality filter, not a
                validation; skills with no category are excluded by it.
            limit: Rows the page may hold.
            offset: Matching rows to skip.

        Returns:
            The page, the total, and the tallies across every matching skill.
        """
        rows = await self._paged(
            lambda skip: self.repositories.list_skills(
                owner.id, category=category, limit=_READ_PAGE_SIZE, offset=skip
            ),
            cap=self.settings.learning_max_skills,
        )
        skip = max(0, offset)
        with_evidence = sum(1 for row in rows if row.evidence_count > 0)
        return SkillListRead(
            items=[SkillRead.model_validate(row) for row in rows[skip : skip + max(1, limit)]],
            total=len(rows),
            limit=limit,
            offset=skip,
            by_level_source={
                source.value: sum(1 for row in rows if row.level_source == source.value)
                for source in SkillLevelSource
            },
            by_category=_tally(row.category for row in rows),
            skills_with_evidence=with_evidence,
            skills_without_evidence=len(rows) - with_evidence,
        )

    async def get_skill(self, *, owner: User, skill_id: uuid.UUID) -> SkillRead:
        """One skill, or a 404 that does not confirm whose it was.

        Args:
            owner: The caller.
            skill_id: The skill to read.

        Returns:
            The skill row as the wire shape.

        Raises:
            NotFoundError: If no row with that id belongs to this account.
        """
        return SkillRead.model_validate(await self._owned_skill(owner=owner, skill_id=skill_id))

    async def update_skill(
        self, *, owner: User, skill_id: uuid.UUID, values: Mapping[str, Any]
    ) -> SkillRead:
        """Edit a skill's claim about itself, and nothing about its evidence.

        ``evidence_count`` and ``last_activity_at`` are refused by the
        repository's write set: they are NEXUS's own observations, and a PATCH
        that could move them would let a skill claim six recorded study sessions
        that do not exist — which the gap read would then quote as the evidence
        behind a level. :meth:`record_activity` is the only writer.

        Sending ``current_level`` re-records the source as ``user_defined``,
        because the person is the one making the claim now. A level that stays
        ``system_estimate`` after the user re-asserts it would keep crediting NEXUS
        for a number the user just typed.

        Args:
            owner: The caller.
            skill_id: The skill to edit.
            values: Column name to new value, from the editable set.

        Returns:
            The updated skill.

        Raises:
            ValidationError: If ``values`` is empty, names a column the skill may
                not write, nulls a column the schema forbids, or puts a level
                outside 1-5.
            ConflictError: If the edit renames the skill onto a name this account
                already tracks. The same answer ``POST`` gives for the same
                mistake, because it is the same mistake: without it the rename
                reaches ``uq_skills_owner_name`` as an ``IntegrityError`` and the
                user is shown a 500 for a typo.
            NotFoundError: If the skill is not this account's.
        """
        if not values:
            raise ValidationError("A skill edit must change at least one field.")
        writes = dict(values)
        if "name" in writes and writes["name"] is None:
            raise ValidationError(
                "A skill cannot be left without a name; omit the field to leave the "
                "name alone."
            )
        # An explicit null on a level *clears the claim* rather than storing SQL
        # NULL: the column is NOT NULL, and "clear it" has to mean the one thing
        # the schema can hold — the floor it had before anybody claimed anything.
        for column, floor in (
            ("current_level", DEFAULT_SKILL_CURRENT_LEVEL),
            ("target_level", DEFAULT_SKILL_TARGET_LEVEL),
        ):
            if writes.get(column, "sentinel") is None:
                writes[column] = floor
        if "current_level" in writes:
            writes["level_source"] = SkillLevelSource.USER_DEFINED.value

        await self._require_free_skill_name(
            owner=owner, skill_id=skill_id, name=writes.get("name")
        )

        try:
            skill = await self.repositories.update_skill(owner.id, skill_id, writes)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        if skill is None:
            raise NotFoundError(_SKILL_NOT_FOUND)

        await self._record_event(
            ActivityEvent.SKILL_UPDATED,
            owner=owner,
            metadata={"skill_id": str(skill.id), "level_source": skill.level_source},
        )
        return SkillRead.model_validate(skill)

    async def _require_free_skill_name(
        self, *, owner: User, skill_id: uuid.UUID | None, name: str | None
    ) -> None:
        """Refuse a name this account already tracks, on create or on rename.

        The check :meth:`create_skill` makes before an insert, reused for the edit
        so a rename to an occupied name answers with the same 409 the create does
        rather than an ``IntegrityError`` the client cannot read. ``skill_id`` is
        the row being renamed: a skill keeping its own name is not a conflict with
        itself, which matters because the lookup matches without regard to case.

        Args:
            owner: The caller.
            skill_id: The row being renamed, or ``None`` on a create.
            name: The name being claimed, or ``None`` when the edit does not
                touch the name at all.

        Raises:
            ConflictError: If another row already holds that name.
        """
        if name is None:
            return
        existing = await self.repositories.get_skill_by_name(owner.id, name)
        if existing is not None and existing.id != skill_id:
            raise ConflictError("That skill is already tracked for this account.")

    async def delete_skill(self, *, owner: User, skill_id: uuid.UUID) -> None:
        """Remove one skill. **Its recorded activities are kept, not cascaded.**

        ``learning_activities.skill_id`` is ``ON DELETE SET NULL``, so the rows
        survive as append-only facts with an unattributed subject — the same state
        they are already in when the user never named a skill. Deleting one skill
        row instead used to remove every activity recorded against it, and this
        table has no ``updated_at``, so nothing recorded that the history had gone.

        What the delete does take is the user's levels, and any career evidence
        that named the skill survives as the user's own claim with the pointer
        dropped. The cost is stated rather than hidden: an activity that outlives
        its skill still counts towards every account-wide figure and stops counting
        towards that skill's evidence count and gap.

        Args:
            owner: The caller.
            skill_id: The skill to remove.

        Raises:
            NotFoundError: If no row with that id belongs to this account.
        """
        skill = await self._owned_skill(owner=owner, skill_id=skill_id)
        if not await self.repositories.delete_skill(owner.id, skill.id):
            raise NotFoundError(_SKILL_NOT_FOUND)

    # ------------------------------------------------------------------
    # Activities — the evidence, append-only
    # ------------------------------------------------------------------

    async def record_activity(
        self,
        *,
        owner: User,
        title: str,
        activity_type: str,
        skill_id: uuid.UUID | None = None,
        goal_id: uuid.UUID | None = None,
        description: str | None = None,
        occurred_at: datetime | None = None,
        duration_minutes: int | None = None,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
    ) -> LearningActivityRead:
        """Record one thing that happened, and count it against the skill.

        Three writes, in this order and for this reason: the activity row first,
        then :meth:`~app.repositories.learning.LearningRepository.record_skill_evidence`,
        then the events. A reader arriving between the first and the second sees
        an activity the skill's own counter has not caught up with yet; the
        reverse order would show a skill claiming evidence for an activity that
        does not exist yet, which is the claim this phase exists to prevent.

        **Nothing here moves a level.** Evidence is collected; the level is
        asserted separately and labelled. ``record_skill_evidence`` bumps a
        counter and a clock and nothing else.

        ``occurred_at`` defaults to the database clock — the only case in this
        module where NEXUS supplies an instant — and the evidence bump moves
        ``last_activity_at`` to the *greatest* of the two, so back-filling last
        week's log cannot make a skill look older than one worked on today.

        Args:
            owner: The caller, and the owner of every row written.
            title: One line naming what was done, in the user's words. Required:
                NEXUS will not invent a description of learning nobody described.
            activity_type: A
                :class:`~app.models.enums.LearningActivityType` member.
                ``resource_viewed`` is the weakest of the seven.
            skill_id: A skill in this account to record this against, or ``None``
                for a session recorded before the skill existed.
            goal_id: A goal in this account this counts towards.
            description: Optional note.
            occurred_at: When it happened. Defaults to the database clock.
            duration_minutes: How long it took, or ``None`` for an *event* rather
                than a span.
            source_type: Which subsystem this came from — the label that stops
                "6 commits touched Python files" being read back as "6 Python
                tasks completed".
            source_id: The row it was derived from.

        Returns:
            The stored activity.

        Raises:
            NotFoundError: If a supplied skill or goal is not this account's.
            ValidationError: If the activity type is outside the vocabulary or a
                negative duration was supplied.
        """
        await self._require_skill(owner=owner, skill_id=skill_id)
        await self._require_goal(owner=owner, goal_id=goal_id)

        try:
            activity = await self.repositories.create_activity(
                owner.id,
                title=title,
                activity_type=activity_type,
                skill_id=skill_id,
                goal_id=goal_id,
                description=description,
                occurred_at=occurred_at,
                duration_minutes=duration_minutes,
                source_type=source_type,
                source_id=source_id,
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error

        await self._record_event(
            ActivityEvent.LEARNING_SESSION_RECORDED,
            owner=owner,
            metadata={
                "activity_id": str(activity.id),
                "activity_type": activity.activity_type,
                "duration_minutes": activity.duration_minutes,
            },
        )
        if skill_id is not None:
            skill = await self.repositories.record_skill_evidence(
                owner.id, skill_id, activity.occurred_at
            )
            if skill is None:
                raise NotFoundError(_SKILL_NOT_FOUND)
            await self._record_event(
                ActivityEvent.SKILL_ACTIVITY_RECORDED,
                owner=owner,
                metadata={
                    "activity_id": str(activity.id),
                    "skill_id": str(skill.id),
                    "evidence_count": skill.evidence_count,
                },
            )
        return LearningActivityRead.model_validate(activity)

    async def list_activities(
        self,
        *,
        owner: User,
        skill_id: uuid.UUID | None = None,
        goal_id: uuid.UUID | None = None,
        activity_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> LearningActivityListRead:
        """One page of the recorded evidence, newest first, with the type tally.

        The tally is complete across every matching activity and carries all seven
        types in weighting order, so a header can say "6 activities: 4 study
        sessions, 2 page views" without a client re-tallying a page slice and
        quoting it as the whole history.

        Args:
            owner: The caller.
            skill_id: Narrow to one skill. Activities naming no skill are
                excluded rather than folded into it.
            goal_id: Narrow to one goal.
            activity_type: Narrow to one
                :class:`~app.models.enums.LearningActivityType` member.
            since: Inclusive lower bound on ``occurred_at``.
            until: Exclusive upper bound. Half-open, matching the activity window:
                a closed window would count an activity landing on the boundary
                twice.
            limit: Rows the page may hold.
            offset: Matching rows to skip.

        Returns:
            The page, the total, and the complete type tally.

        Raises:
            ValidationError: If ``activity_type`` is outside the vocabulary. The
                repository is where that check lives, and a ``ValueError``
                escaping it would take the read down as a 500 over a query
                parameter rather than answering with the 422 the contract
                promises.
        """
        try:
            rows = await self._paged(
                lambda skip: self.repositories.list_activities(
                    owner.id,
                    skill_id=skill_id,
                    goal_id=goal_id,
                    activity_type=activity_type,
                    since=since,
                    until=until,
                    limit=_READ_PAGE_SIZE,
                    offset=skip,
                ),
                cap=_MAX_ACTIVITY_ROWS,
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error
        skip = max(0, offset)
        by_type = {
            member.value: sum(1 for row in rows if row.activity_type == member.value)
            for member in LearningActivityType
        }
        return LearningActivityListRead(
            items=[
                LearningActivityRead.model_validate(row)
                for row in rows[skip : skip + max(1, limit)]
            ],
            total=len(rows),
            limit=limit,
            offset=skip,
            by_type=by_type,
            summary=band_count_sentence(
                by_type, len(rows), _ACTIVITY_LIST_SUBJECT, _ACTIVITY_TYPE_ORDER
            ),
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def summary(self, *, owner: User, window_days: int | None = None) -> LearningSummaryRead:
        """The dashboard's headline counts, and one factual sentence about them.

        Counts only, over a window whose length is carried beside them so the
        sentence underneath can be true. There is no score and no verdict here.

        ``minutes_in_window`` is null when nothing in the window carried a
        duration, because ``0`` would claim time was measured and found to be
        nothing. ``has_data`` is the cold-start flag: false when nothing has ever
        been recorded, so a dashboard of zeroes reads as an absence rather than as
        a finding about the account.

        Args:
            owner: The caller.
            window_days: Length of the window. Defaults to
                ``learning_default_window_days`` and is refused above
                ``learning_max_window_days``.

        Returns:
            The summary.

        Raises:
            ValidationError: If the window exceeds the configured ceiling.
                Silently shortening it would make the returned ``window_days``
                disagree with what the caller asked for.
        """
        days, window_start, window_end = await self._resolve_window(window_days)
        goal_count = await self.repositories.count_goals(owner.id)
        open_count = sum(
            [
                await self.repositories.count_goals(owner.id, status=status)
                for status in OPEN_GOAL_STATUSES
            ]
        )
        completed = await self.repositories.count_goals(
            owner.id, status=LearningGoalStatus.COMPLETED
        )
        skills = await self._skills(owner.id)
        history = await self.repositories.activity_totals(owner.id)
        window = await self.repositories.activity_totals(
            owner.id, since=window_start, until=window_end
        )
        with_evidence = sum(1 for row in skills if row.evidence_count > 0)
        has_data = history.activities > 0
        return LearningSummaryRead(
            goal_count=goal_count,
            active_goal_count=open_count,
            completed_goal_count=completed,
            skill_count=len(skills),
            skills_with_evidence=with_evidence,
            activity_count=history.activities,
            activities_in_window=window.activities,
            minutes_in_window=window.measured_minutes,
            window_days=days,
            window_start=window_start,
            window_end=window_end,
            latest_activity_at=_as_utc(history.last_occurred_at),
            has_data=has_data,
            summary=_summary_sentence(
                goal_count=goal_count,
                skill_count=len(skills),
                activities_in_window=window.activities,
                window_days=days,
                has_data=has_data,
            ),
        )

    async def metrics(
        self, *, owner: User, window_days: int | None = None
    ) -> tuple[LearningMetricRead, ...]:
        """The eight metrics, each fully explained, over one window.

        Every figure comes from
        :func:`app.services.learning.metrics.build_metrics` fed with real rows, so
        all eight describe the same window and the same rows. The one input the
        pure module refuses to derive is supplied here: the window's end instant,
        taken from the database clock rather than from the host.

        Args:
            owner: The caller.
            window_days: Length of the window.

        Returns:
            Exactly eight metrics, in contract order. One that could not be
            measured comes back with ``value=None`` and a reason — never omitted,
            because a client indexing by key would render a hole.

        Raises:
            ValidationError: If the window exceeds the configured ceiling.
        """
        _days, window_start, window_end = await self._resolve_window(window_days)
        computed = build_metrics(
            await self._activity_samples(owner.id),
            _goal_samples(await self._goals(owner.id)),
            window_start=window_start,
            window_end=window_end,
        )
        return tuple(_metric_read(metric) for metric in computed)

    async def gaps(
        self, *, owner: User, limit: int = _DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> SkillGapListRead:
        """Every skill's distance from its target, computed on read and never stored.

        The arithmetic is
        :func:`app.services.learning.gaps.skill_gaps`', fed with this account's
        skills and its recorded activities; nothing here subtracts a level. The
        settings value ``learning_min_evidence_for_estimate`` is passed in as the
        threshold below which NEXUS refuses to present a level it inferred itself,
        which keeps the refusal a deployment setting rather than a constant
        buried in a service.

        Ordering is widest gap first, then alphabetical. An unavailable gap keeps
        its own ``gap`` figure and is **not** filtered out: "5 gaps, 1 not measured
        yet" is a true sentence about the page, and dropping the unmeasured row
        would make the list quietly shorter every week with nothing said about
        what was lost.

        Args:
            owner: The caller.
            limit: Gaps the page may hold.
            offset: Gaps to skip.

        Returns:
            The page, with the measured/unmeasured split beside it.
        """
        now = await self._now()
        rows = await self._skills(owner.id)
        computed = skill_gaps(
            [_skill_sample(row) for row in rows],
            now=now,
            activities=await self._activity_samples(owner.id),
            min_evidence_for_estimate=self.settings.learning_min_evidence_for_estimate,
        )
        ordered = sorted(computed, key=lambda gap: (-gap.gap, gap.skill_name))
        skip = max(0, offset)
        available = sum(1 for gap in ordered if gap.available)
        return SkillGapListRead(
            items=[
                SkillGapRead.model_validate(gap) for gap in ordered[skip : skip + max(1, limit)]
            ],
            total=len(ordered),
            limit=limit,
            offset=skip,
            available_count=available,
            unavailable_count=len(ordered) - available,
            by_level_source={
                source.value: sum(1 for gap in ordered if gap.level_source is source)
                for source in SkillLevelSource
            },
        )

    async def read_activity(
        self,
        *,
        owner: User,
        window_days: int | None = None,
        granularity: str | None = None,
        skill_id: uuid.UUID | None = None,
        goal_id: uuid.UUID | None = None,
    ) -> LearningActivitySeriesRead:
        """Recorded activities bucketed day, week or month, with every gap zero-filled.

        Named ``read_activity`` because ``self.activity`` is the optional event
        sink; a method of the same name would be shadowed by that attribute and the
        read behind ``GET /learning/activity`` would silently become ``None``.

        The buckets are dense by construction because
        :func:`app.services.learning.metrics.activity_series` emits one per period
        across the whole range. A chart built only from the periods that have
        sessions would skip a quiet Tuesday, and a reader counting the bars would
        see five active days and read them as consecutive.

        A bucket's ``minutes`` is **null** when nothing in it carried a duration.
        ``0`` would claim time was measured and found to be nothing, which is a far
        more confident claim than "nobody said how long it took".

        Args:
            owner: The caller.
            window_days: Length of the range. Defaults to
                ``learning_default_window_days``.
            granularity: ``day``, ``week`` or ``month``. Defaults to ``day``.
            skill_id: Narrow to one skill.
            goal_id: Narrow to one goal.

        Returns:
            The series, with the window and the scope that produced it.

        Raises:
            ValidationError: If the window exceeds the ceiling or the granularity
                is unknown. Both are facts about the request and are better
                refused than rendered as an empty chart.
            NotFoundError: If a supplied skill or goal is not this account's.
        """
        days, window_start, window_end = await self._resolve_window(window_days)
        step = _granularity(granularity or _DEFAULT_GRANULARITY)
        await self._require_skill(owner=owner, skill_id=skill_id)
        await self._require_goal(owner=owner, goal_id=goal_id)

        samples = [
            sample
            for sample in await self._activity_samples(owner.id)
            if (skill_id is None or sample.skill_id == skill_id)
            and (goal_id is None or sample.goal_id == goal_id)
        ]
        series = activity_series(
            samples,
            window_start=window_start,
            window_end=window_end,
            granularity=step.value,
            skill_id=skill_id,
            goal_id=goal_id,
        )
        grouped: dict[str, list[ActivitySample]] = {}
        for sample in samples:
            grouped.setdefault(bucket_for(sample.occurred_at, step.value), []).append(sample)

        starts = [bucket.start for bucket in series.buckets]
        buckets: list[LearningActivityBucketRead] = []
        for index, bucket in enumerate(series.buckets):
            inside = grouped.get(bucket.label, ())
            buckets.append(
                LearningActivityBucketRead(
                    bucket_start=bucket.start,
                    bucket_end=(starts[index + 1] if index + 1 < len(starts) else window_end),
                    activities=bucket.activity_count,
                    sessions=sum(1 for sample in inside if sample.activity_type == _STUDY_SESSION),
                    minutes=(
                        bucket.session_minutes
                        if any(sample.duration_minutes is not None for sample in inside)
                        else None
                    ),
                )
            )
        timed = [bucket.minutes for bucket in buckets if bucket.minutes is not None]
        return LearningActivitySeriesRead(
            granularity=series.granularity,
            window_days=days,
            window_start=window_start,
            window_end=window_end,
            skill_id=skill_id,
            buckets=buckets,
            total_activities=sum(bucket.activities for bucket in buckets),
            total_minutes=sum(timed) if timed else None,
        )

    async def features(
        self, *, owner: User, window_days: int | None = None
    ) -> LearningFeatureVectorRead:
        """The ML-ready feature row: eight named numbers under a schema version.

        **An extractor, not a model.** Named numbers, so a later trainer knows what
        each column meant. Nothing here is a prediction, a probability or a fitted
        parameter, and this phase trains, loads and serves nothing.

        Every figure is read off the same
        :func:`app.services.learning.metrics.build_metrics` call that
        ``GET /learning/metrics`` returns, so the card and the export cannot
        disagree — and the one that is *not* a metric, ``learning_minutes``, comes
        from :meth:`~app.repositories.learning.LearningRepository.activity_totals`,
        which leaves ``SUM`` over no durations uncoalesced precisely so the null
        survives.

        **A figure that could not be computed is null, never 0.** An account with
        no goals does not have goals that are zero percent complete; an empty
        completion-rate denominator is not a completion rate of 0.0; and nothing
        recorded in the window is not a measured consistency of 0. Inside a
        training matrix a fabricated zero is indistinguishable from an observed
        one.

        Args:
            owner: The caller.
            window_days: Length of the window the date-bounded figures cover.

        Returns:
            The vector, stamped ``learning_features.v1`` and the database clock's
            ``generated_at``.

        Raises:
            ValidationError: If the window exceeds the configured ceiling.
        """
        days, window_start, window_end = await self._resolve_window(window_days)
        computed = build_metrics(
            await self._activity_samples(owner.id),
            _goal_samples(await self._goals(owner.id)),
            window_start=window_start,
            window_end=window_end,
        )
        by_key = {metric.key: metric for metric in computed}
        totals = await self.repositories.activity_totals(
            owner.id, since=window_start, until=window_end
        )
        return LearningFeatureVectorRead(
            generated_at=window_end,
            window_days=days,
            features=LearningFeatureValues(
                sessions_last_7d=int(by_key[MetricKey.SESSIONS_LAST_7D.value].value),
                sessions_last_30d=int(by_key[MetricKey.SESSIONS_LAST_30D.value].value),
                learning_minutes=totals.measured_minutes,
                goal_progress=_measured(by_key[MetricKey.GOAL_PROGRESS.value]),
                goal_deadline_distance_days=_measured_days(
                    by_key[MetricKey.GOAL_DEADLINE_DISTANCE_DAYS.value]
                ),
                completion_rate=_measured(by_key[MetricKey.COMPLETION_RATE.value]),
                learning_consistency=(
                    None
                    if totals.activities == 0
                    else _measured(by_key[MetricKey.LEARNING_CONSISTENCY.value])
                ),
                skill_activity_frequency=_measured(
                    by_key[MetricKey.SKILL_ACTIVITY_FREQUENCY.value]
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _owned_goal(self, *, owner: User, goal_id: uuid.UUID) -> LearningGoal:
        """Resolve ``goal_id`` through the scoped lookup, or 404.

        Another account's goal is not a permission error here; it is a row that
        does not exist, identically to an id nobody has ever issued.
        """
        goal = await self.repositories.get_goal(owner.id, goal_id)
        if goal is None:
            raise NotFoundError(_GOAL_NOT_FOUND)
        return goal

    async def _owned_skill(self, *, owner: User, skill_id: uuid.UUID) -> Skill:
        """Resolve ``skill_id`` through the scoped lookup, or 404.

        The single-account corollary of rule 5: knowing a uuid buys nothing,
        because the owner predicate is part of the lookup rather than a check
        afterwards.
        """
        skill = await self.repositories.get_skill(owner.id, skill_id)
        if skill is None:
            raise NotFoundError(_SKILL_NOT_FOUND)
        return skill

    async def _require_skill(self, *, owner: User, skill_id: uuid.UUID | None) -> None:
        """Prove a supplied ``skill_id`` is this account's, or 404. No-op for ``None``.

        Called **before** the write, so a goal, an activity or a PATCH never
        stores a pointer to somebody else's row and then reports success.
        """
        if skill_id is not None:
            await self._owned_skill(owner=owner, skill_id=skill_id)

    async def _require_goal(self, *, owner: User, goal_id: uuid.UUID | None) -> None:
        """Prove a supplied ``goal_id`` is this account's, or 404. No-op for ``None``."""
        if goal_id is not None:
            await self._owned_goal(owner=owner, goal_id=goal_id)

    async def _require_project(self, *, owner: User, project_id: uuid.UUID | None) -> None:
        """Prove a supplied ``project_id`` is this account's, or 404.

        A 403 would confirm the id exists and turn the endpoint into a probe for
        which project ids are real.
        """
        if project_id is not None and (
            await self.projects.get_by_id_for_user(project_id, owner.id) is None
        ):
            raise NotFoundError(_PROJECT_NOT_FOUND)

    async def _require_note(self, *, owner: User, note_id: uuid.UUID | None) -> None:
        """Prove a supplied ``note_id`` is this account's, or 404."""
        if note_id is not None and (await self.notes.get_by_id_for_user(note_id, owner.id) is None):
            raise NotFoundError(_NOTE_NOT_FOUND)

    async def _goals(self, owner_id: uuid.UUID) -> list[LearningGoal]:
        """Every goal this account owns, read in bounded pages.

        ``learning_max_goals`` is the bound: the cap that stops a create is the
        same number that stops this sweep, so the set a metric reads over is the
        set the account could legally have written.
        """
        return await self._paged(
            lambda skip: self.repositories.list_goals(owner_id, limit=_READ_PAGE_SIZE, offset=skip),
            cap=self.settings.learning_max_goals,
        )

    async def _skills(self, owner_id: uuid.UUID) -> list[Skill]:
        """Every skill this account owns, read in bounded pages."""
        return await self._paged(
            lambda skip: self.repositories.list_skills(
                owner_id, limit=_READ_PAGE_SIZE, offset=skip
            ),
            cap=self.settings.learning_max_skills,
        )

    async def _activity_samples(self, owner_id: uuid.UUID) -> list[ActivitySample]:
        """Every recorded activity as a :class:`ActivitySample`, newest first.

        Rows are reduced once and handed to both the metrics and the gap module, so
        there is exactly one definition of "a session" in the codebase rather than
        one per consumer. See :data:`_MAX_ACTIVITY_ROWS` for the bound and why
        truncating the newest rows is the safe direction to truncate in.
        """
        rows: list[LearningActivity] = []
        while len(rows) < _MAX_ACTIVITY_ROWS:
            page, _total = await self.repositories.list_activities(
                owner_id, limit=_READ_PAGE_SIZE, offset=len(rows)
            )
            rows.extend(page)
            if len(page) < _READ_PAGE_SIZE:
                break
        return _samples_from_rows(rows)

    async def _paged(
        self, page_of: Callable[[int], Awaitable[tuple[list[_T], int]]], *, cap: int
    ) -> list[_T]:
        """Read a complete owner-scoped set in bounded pages, or up to ``cap``.

        One statement can only return :data:`_READ_PAGE_SIZE` rows, so a set
        larger than that is read in pages of exactly that size rather than being
        silently truncated to one page. ``cap`` is the configured ceiling on the
        set — ``learning_max_goals`` or ``learning_max_skills`` — and the account
        cannot legally hold more than that, so the sweep terminates.

        Args:
            page_of: ``(offset) -> awaitable`` returning one page and the total the
                filters match. The total is ignored here because the callers that
                print one read it from the length of the set actually read.
            cap: The largest number of rows worth reading.

        Returns:
            The rows, in the order the repository ordered them.
        """
        rows: list[_T] = []
        while len(rows) < cap:
            page, _total = await page_of(len(rows))
            rows.extend(page)
            if len(page) < _READ_PAGE_SIZE:
                break
        return rows

    def _window_days(self, window_days: int | None) -> int:
        """Resolve a requested window length, refusing one above the ceiling.

        Rejected rather than clamped: silently shortening it would make the
        ``window_days`` on the response disagree with what the caller asked for,
        which is the kind of quiet disagreement this codebase treats as a bug.
        """
        requested = (
            self.settings.learning_default_window_days if window_days is None else int(window_days)
        )
        ceiling = max(1, self.settings.learning_max_window_days)
        if requested > ceiling:
            raise ValidationError(f"A window may span at most {ceiling} days.")
        return max(1, requested)

    async def _resolve_window(self, window_days: int | None) -> tuple[int, datetime, datetime]:
        """``(days, inclusive start, exclusive end)`` anchored on the DB clock.

        Half-open at the top so an activity landing exactly on the boundary belongs
        to one window rather than two, and anchored on ``now()`` rather than on the
        host clock so the figures sit on the same timeline as the rows they read.
        """
        days = self._window_days(window_days)
        end = await self._now()
        return days, end - timedelta(days=days), end

    async def _now(self) -> datetime:
        """The database clock.

        Never ``datetime.now()``: a host whose clock drifts from the server's would
        file a session on the wrong day, and the day it landed on is exactly the
        figure ``learning_consistency`` counts.
        """
        value = await self.repositories.session.scalar(select(func.now()))
        return _as_utc(value) if isinstance(value, datetime) else datetime.now(UTC)

    async def _record_event(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        project_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one history row, or return immediately when there is no sink.

        Metadata is ids, counts and vocabulary strings only. A goal title, a skill
        name and a skill description are the user's own words and belong on the row
        the event points at; duplicating them into a feed nobody asked for is how a
        summary view ends up quoting stale text, and the feed is the least
        access-controlled surface the product has. A caller who needs the words
        follows the id.
        """
        if self.activity is None:
            return
        await self.activity.record(
            event.value, user_id=owner.id, project_id=project_id, metadata=metadata
        )


#: The one activity type the series counts separately, because "6 sessions" and
#: "6 activities" answer different questions and a chart showing only the second
#: would overstate a fortnight of page views. Mirrors the bucket field's own
#: documentation rather than the enum's ordering, which is the weighting order.


def _granularity(value: str) -> ActivityGranularity:
    """Coerce a requested bucket size, refusing anything unknown.

    Raises:
        ValidationError: If it is not ``day``, ``week`` or ``month``. Guessing one
            would silently return a chart nobody asked for.
    """
    try:
        return ActivityGranularity(value)
    except ValueError:
        raise ValidationError(
            f"Unsupported granularity {value!r}; expected one of "
            f"{', '.join(ACTIVITY_GRANULARITIES)}."
        ) from None


def _tally(values: Iterable[str | None]) -> dict[str, int]:
    """Count each non-null value.

    Nulls are skipped rather than counted under a ``None`` key: a skill with no
    category is not in a category, and a tally carrying ``"null": 7`` would invite
    a client to render an "uncategorised" band NEXUS never defined.
    """
    counts: dict[str, int] = {}
    for value in values:
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _skill_sample(row: Skill) -> SkillSample:
    """Reduce a ``skills`` row to the fields a gap reads.

    ``level_source`` is re-read through the enum rather than passed as the stored
    string, because the gap explanation has to be built from one of exactly two
    phrases and a bare string is how that guarantee would be lost.
    """
    return SkillSample(
        name=row.name,
        current_level=row.current_level,
        target_level=row.target_level,
        level_source=SkillLevelSource(row.level_source),
        skill_id=row.id,
        evidence_count=row.evidence_count,
        last_activity_at=_as_utc(row.last_activity_at),
    )


def _samples_from_rows(rows: Sequence[LearningActivity]) -> list[ActivitySample]:
    """Reduce ``learning_activities`` rows to the fields the metrics and gaps read."""
    return [
        ActivitySample(
            occurred_at=_as_utc(row.occurred_at),
            activity_id=row.id,
            activity_type=row.activity_type,
            skill_id=row.skill_id,
            goal_id=row.goal_id,
            duration_minutes=row.duration_minutes,
        )
        for row in rows
    ]


def _goal_samples(rows: Sequence[LearningGoal]) -> list[GoalSample]:
    """Reduce ``learning_goals`` rows to the fields the goal metrics read.

    ``progress`` is carried through exactly as stored. It is the user's own
    percentage and is never recomputed from the activities beneath it.
    """
    return [
        GoalSample(
            goal_id=row.id,
            status=LearningGoalStatus(row.status),
            progress=row.progress,
            target_date=row.target_date,
        )
        for row in rows
    ]


def _metric_read(metric: LearningMetric) -> LearningMetricRead:
    """Map a pure :class:`LearningMetric` onto the wire shape.

    The one place ``value`` becomes nullable, and the conversion is deliberately
    conditional on ``available``: an unavailable metric carries a meaningless
    ``0.0`` internally because the dataclass requires a float, and a client that
    rendered it would be showing a fabricated zero. A *measured* zero keeps both
    its value and its ``available=True``, because "nothing was recorded in these
    thirty days" is a true sentence and must survive the trip.
    """
    return LearningMetricRead(
        key=metric.key,
        label=metric.label,
        value=metric.value if metric.available else None,
        unit=metric.unit,
        definition=metric.definition,
        window_days=metric.window_days,
        source=metric.source,
        explanation=metric.explanation,
        available=metric.available,
        reason_if_unavailable=metric.reason_if_unavailable,
    )


def _measured(metric: LearningMetric) -> float | None:
    """The metric's figure, or ``None`` when it declined to measure one.

    The single place the null-not-zero rule is applied to the metric values, so a
    future feature cannot quietly read ``metric.value`` and publish the ``0.0`` an
    unavailable metric carries internally.
    """
    return None if not metric.available else float(metric.value)


def _measured_days(metric: LearningMetric) -> int | None:
    """The metric's day figure as whole days, or ``None`` when it declined.

    ``goal_deadline_distance_days`` is the mean number of days to the dated open
    goals' deadlines, and the feature column is an integer, so the mean is rounded
    rather than re-derived from the rows: a figure computed here from the same
    goals by a different aggregation would let ``/learning/metrics`` and
    ``/learning/features`` disagree about the same window.
    """
    value = _measured(metric)
    return None if value is None else round(value)


def _summary_sentence(
    *,
    goal_count: int,
    skill_count: int,
    activities_in_window: int,
    window_days: int,
    has_data: bool,
) -> str:
    """One factual sentence describing the counts.

    Three registers, chosen by what is actually true rather than by what would
    read best: nothing recorded, something recorded but nothing recent, and
    measured activity in the window. Every sentence carries its own figures, and
    none of them claims effort, ability or progress — a recorded activity is
    evidence that something was recorded, and nothing on this surface converts that
    into a claim about a person.
    """
    if not has_data:
        return (
            "No learning activity has been recorded yet, so there is nothing to "
            "report beyond what is on the page."
        )
    if activities_in_window == 0:
        return (
            f"{goal_count} goal(s) and {skill_count} skill(s) are recorded, and no "
            f"learning activity was recorded in the last {window_days} days."
        )
    return (
        f"{activities_in_window} learning activit{'y' if activities_in_window == 1 else 'ies'} "
        f"were recorded in the last {window_days} days, against {goal_count} goal(s) "
        f"and {skill_count} skill(s) on the page."
    )
