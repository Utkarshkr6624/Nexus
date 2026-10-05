"""Deterministic fixtures for the Phase 6 analytics tests.

Every figure the analytics engine reports is a function of rows that exist in
``tasks``, ``work_sessions``, ``calendar_events``, ``notes`` and
``activity_events``. A test that wants to assert "completion rate is 80%" has to
put ten tasks in the database, eight of them completed, and nothing else that
could move the number. That is what this module is for: it writes the rows
directly, bypassing the API's own validation, so a fixture cannot be perturbed
by whatever a request schema happens to require this month.

Two things make the numbers exact:

* **Instants are explicit, never "now".** Every helper takes the datetime the
  row should carry and passes it through, overriding the ``server_default
  = now()`` on :class:`~app.db.base.TimestampMixin`. The alternative — sleeping,
  or freezing the clock — makes a test's expected values depend on the wall
  clock, and a suite that fails only at midnight is worse than no suite.
* **Days are the server's days.** ``daily_metrics.metric_date`` is cut at the
  **database's** local midnight (see :mod:`app.models.analytics` and
  :func:`app.repositories.analytics.local_day`), so the helpers here take
  UTC instants via :func:`at` and never introduce a local offset. That keeps a
  fixture independent of the server's zone: :func:`at` builds a wall-clock hour
  *in UTC*, and the test that cares where the boundary falls builds its instants
  in the server's zone instead —
  ``_local_instant`` in ``test_analytics_daily_metrics.py`` and in
  ``test_analytics_day_agreement.py``. Writing "Days are UTC days" here, as this
  module once did, is what let six suites go on assuming a UTC cut they were
  never asserting.

The builder is a plain object rather than a set of pytest fixtures because each
test needs a different shape of data, and a fixture per shape would be a
fixture per assertion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivityLog
from app.models.enums import ActivityEvent, ProjectStatus, TaskPriority, TaskStatus
from app.models.knowledge import Note
from app.models.planner import CalendarEvent, WorkSession
from app.models.project import Project
from app.models.task import Task
from app.models.user import User

__all__ = [
    "ACCOUNT",
    "DAY",
    "PASSWORD",
    "AnalyticsSeed",
    "at",
    "bearer",
    "register_user",
    "register_via_api",
    "seeded_client",
    "sign_in",
    "user_by_email",
]


#: The anchor every relative helper counts from: 2026-01-05, a Monday.
#:
#: Monday matters more than it looks. The analytics engine buckets by weekday
#: for time distribution and cuts weeks at Monday for velocity, so a fixture
#: anchored mid-week silently produces fixtures that straddle a bucket
#: boundary and never reproduce on a re-run.
DAY = date(2026, 1, 5)


def at(day: date, hour: int = 9, minute: int = 0) -> datetime:
    """A timezone-aware UTC instant on ``day``.

    Deliberately *not* built in the database's zone, and worth saying why that is
    still correct now that :func:`app.repositories.analytics.local_day` buckets in
    that zone. What a day-bucketed assertion needs is that the row lands on
    ``day``, and an instant at ``hour`` UTC lands on ``day`` in the server's
    calendar for every offset under fifteen hours — which is every zone in
    practical use. So the helpers here stay zone-independent, and a suite does
    not change meaning when the server's ``TimeZone`` changes.

    The price is that ``hour`` is a UTC hour: ``at(day, 23)`` is 04:30 the next
    morning in ``Asia/Calcutta``. A test whose claim *is* about the hour — about
    the cut, about a time-of-day feature — must not use this helper and must ask
    the database for its zone first, the way ``_local_instant`` does in
    ``test_analytics_daily_metrics.py`` and ``test_analytics_day_agreement.py``.
    """
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


async def register_user(
    session: AsyncSession,
    *,
    username: str = "ada",
    email: str | None = None,
    role: str = "user",
) -> User:
    """Insert a user row directly.

    Analytics endpoints authenticate with a bearer token, so most tests pair
    this with a real ``POST /auth/register``+``login`` round trip rather than
    minting a token for a row made here. It exists for the cases that need a
    *second* user (isolation tests) without paying for another HTTP call, and
    for seeding directly against the service rather than the API.
    """
    user = User(
        username=username,
        email=email or f"{username}@nexus.test",
        hashed_password="not-a-real-hash",  # noqa: S106 — a fixture, never logged in
        role=role,
    )
    session.add(user)
    await session.commit()
    return user


class AnalyticsSeed:
    """Builds one user's activity, day by day, and reads it back.

    A seed is constructed per test against the ``db_session`` fixture. Every
    helper is ``async`` and commits what it adds, so a test that seeds and then
    reads through the API sees durable rows — the same contract the
    repositories have, which is what makes a fixture usable either through
    HTTP or straight against the service.
    """

    def __init__(self, session: AsyncSession, owner: User) -> None:
        self.session = session
        self.owner = owner
        self._counter = 0

    # -- internals -----------------------------------------------------------

    def _next(self, prefix: str) -> str:
        """A unique, readable label.

        Uniqueness is not cosmetic: ``projects.name`` and ``tasks.title`` are
        free text, and a test that re-runs against a truncated table is fine
        with a collision — but a fixture that seeds the same project name twice
        in one test to represent "two projects" would silently merge them.
        """
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def _add(self, instance: Any) -> Any:
        self.session.add(instance)
        return instance

    async def flush(self) -> None:
        """Commit everything added so far.

        The repositories commit as they go, so a test that seeds and then reads
        through the API needs the rows durable rather than merely pending. Every
        seeding helper is therefore ``async`` and awaits this.
        """
        await self.session.commit()

    # -- rows ----------------------------------------------------------------

    async def project(
        self,
        *,
        name: str | None = None,
        status: str = ProjectStatus.ACTIVE.value,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> Project:
        """A project, defaulting to active and created on the anchor day."""
        project = Project(
            owner_id=self.owner.id,
            name=name or self._next("project"),
            status=status,
            created_at=created_at or at(DAY),
            completed_at=completed_at,
        )
        self._add(project)
        await self.flush()
        return project

    async def task(
        self,
        *,
        project_id: uuid.UUID,
        title: str | None = None,
        status: str = TaskStatus.TODO.value,
        priority: str = TaskPriority.MEDIUM.value,
        created_at: datetime | None = None,
        due_date: date | None = None,
        estimated_minutes: int | None = None,
        actual_minutes: int = 0,
        completed_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> Task:
        """A task with every field the analytics engine reads.

        ``project_id`` is **required**, not optional. ``tasks.project_id`` is
        ``NOT NULL`` with an ``ON DELETE CASCADE`` to ``projects`` — a task
        belongs to a project in this schema, so a project-less task cannot be
        seeded at all. Making the argument mandatory rather than defaulting it
        to ``None`` turns that into a clear ``TypeError`` at the call site
        instead of a ``NotNullViolation`` twenty lines later, somewhere
        unrelated.

        ``created_at`` and ``updated_at`` are the two that carry weight:
        completions are bucketed by ``completed_at``, cancellations by
        ``updated_at`` (there is no ``cancelled_at`` column anywhere in the
        schema), and creations by ``created_at``. They are separate parameters
        because a task created on Monday and cancelled on Friday must be two
        days' facts, not one row's single timestamp.
        """
        values: dict[str, Any] = {
            "owner_id": self.owner.id,
            "project_id": project_id,
            "title": title or self._next("task"),
            "status": status,
            "priority": priority,
            "created_at": created_at or at(DAY),
            "due_date": due_date,
            "estimated_minutes": estimated_minutes,
            "actual_minutes": actual_minutes,
            "completed_at": completed_at,
        }
        if updated_at is not None:
            values["updated_at"] = updated_at
        task = Task(**values)
        self._add(task)
        await self.flush()
        return task

    async def completed_task(
        self,
        *,
        day: date,
        project_id: uuid.UUID,
        estimated_minutes: int | None = None,
        actual_minutes: int = 0,
        due_date: date | None = None,
        completed_hour: int = 12,
    ) -> Task:
        """A task created and finished on ``day`` — the common fixture.

        ``due_date`` defaults to ``day`` so the task counts as *on time*: the
        deadline-adherence maths compares the completion instant against the
        due date, and a test that wants a late completion has to say so
        explicitly rather than inherit an accidental pass.
        """
        return await self.task(
            project_id=project_id,
            status=TaskStatus.COMPLETED.value,
            created_at=at(day),
            completed_at=at(day, completed_hour),
            due_date=day if due_date is None else due_date,
            estimated_minutes=estimated_minutes,
            actual_minutes=actual_minutes,
        )

    async def work_session(
        self,
        *,
        day: date,
        minutes: int,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        start_hour: int = 9,
        status: str = "completed",
        estimated_minutes: int | None = None,
    ) -> WorkSession:
        """A finished work session of exactly ``minutes`` on ``day``.

        Both planned and actual ends are written, ``minutes`` apart, so
        ``planned_minutes`` and ``actual_minutes`` in the daily aggregate agree
        with each other by construction. A fixture that only filled one of the
        two would be testing an asymmetry the product does not have.
        """
        start = at(day, start_hour)
        session = WorkSession(
            owner_id=self.owner.id,
            task_id=task_id,
            project_id=project_id,
            scheduled_start=start,
            scheduled_end=start + timedelta(minutes=minutes),
            actual_start=start,
            actual_end=start + timedelta(minutes=minutes),
            estimated_minutes=estimated_minutes,
            actual_minutes=minutes,
            status=status,
            created_at=start,
        )
        self._add(session)
        await self.flush()
        return session

    async def calendar_event(
        self,
        *,
        day: date,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        start_hour: int = 9,
        minutes: int = 60,
    ) -> CalendarEvent:
        """A calendar entry on ``day``."""
        start = at(day, start_hour)
        event = CalendarEvent(
            owner_id=self.owner.id,
            project_id=project_id,
            task_id=task_id,
            title=self._next("event"),
            starts_at=start,
            ends_at=start + timedelta(minutes=minutes),
            created_at=start,
        )
        self._add(event)
        await self.flush()
        return event

    async def activity(
        self,
        event_type: str,
        *,
        day: date,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        hour: int = 10,
        metadata: dict[str, Any] | None = None,
    ) -> ActivityLog:
        """One ``activity_events`` row.

        Blocked, rescheduled and knowledge counts are all read from here rather
        than from a column on the task, because a task can be blocked and
        unblocked repeatedly and only the event log records that.
        """
        row = ActivityLog(
            user_id=self.owner.id,
            project_id=project_id,
            task_id=task_id,
            event_type=(
                event_type.value if isinstance(event_type, ActivityEvent) else str(event_type)
            ),
            metadata_=metadata or {},
            created_at=at(day, hour),
        )
        self._add(row)
        await self.flush()
        return row

    async def note(
        self,
        *,
        day: date,
        status: str = "draft",
        title: str | None = None,
        updated_at: datetime | None = None,
    ) -> Note:
        """A knowledge note, which is the Phase 5 input to knowledge analytics."""
        values: dict[str, Any] = {
            "owner_id": self.owner.id,
            "title": title or self._next("note"),
            "content": "seeded body",
            "status": status,
            "created_at": at(day),
        }
        if updated_at is not None:
            values["updated_at"] = updated_at
        note = Note(**values)
        self._add(note)
        await self.flush()
        return note


def bearer(token: str) -> dict[str, str]:
    """The auth header for a token, spelled once so no test invents its own."""
    return {"Authorization": f"Bearer {token}"}


#: A password strong enough to clear the registration policy, reused so a test
#: never fails on policy drift instead of on the metric it was written for.
PASSWORD = "Analytics-Fixture-7"

#: The account payload for :func:`register_via_api`. Email domains differ from
#: :func:`register_user`'s so the two never collide when a test mixes them.
ACCOUNT = {"username": "ada", "email": "ada@nexus.test", "password": PASSWORD}


async def register_via_api(
    client: Any,
    *,
    username: str = "ada",
    email: str | None = None,
    password: str = PASSWORD,
) -> dict[str, Any]:
    """Create an account through the real endpoint and return the token pair.

    A row made by :func:`register_user` cannot be logged into — its password
    hash is deliberately fake — so any test that drives the HTTP layer has to
    come through here. Doing the round trip rather than minting a token
    deliberately keeps the auth path in the test: a token forged around the
    auth code proves nothing about the analytics route that consumes it.
    """
    payload = {
        "username": username,
        "email": email or f"{username}@nexus.test",
        "password": password,
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def sign_in(
    client: Any,
    *,
    email: str = "ada@nexus.test",
    password: str = PASSWORD,
) -> dict[str, Any]:
    """Exchange credentials for a token pair."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def user_by_email(session: AsyncSession, email: str) -> User:
    """The ORM row behind an account created through the API.

    Seeding needs the :class:`~app.models.user.User` object — an ``id`` the
    analytics queries filter on — and reading it back is what ties the seeded
    rows to the account that is going to request them. Without this a test can
    register over HTTP, seed against a *different* row, and watch every
    assertion pass against an empty dashboard.
    """
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    return user


async def seeded_client(
    client: Any,
    db_session: AsyncSession,
    *,
    username: str = "ada",
    email: str | None = None,
) -> tuple[AnalyticsSeed, dict[str, str]]:
    """The one-call setup: a signed-in account and a seed bound to it.

    Returns the seed and the auth headers, in that order, because that is the
    order a test uses them in::

        seed, auth = await seeded_client(client, db_session)
        project = await seed.project()
        response = await client.get("/api/v1/analytics/overview", headers=auth)
    """
    address = email or f"{username}@nexus.test"
    await register_via_api(client, username=username, email=address)
    tokens = await sign_in(client, email=address)
    owner = await user_by_email(db_session, address)
    return AnalyticsSeed(db_session, owner), bearer(tokens["access_token"])
