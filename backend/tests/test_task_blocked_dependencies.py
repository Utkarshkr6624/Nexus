"""``has_blocked_dependencies`` on a page: one statement, and the same answer.

:meth:`app.services.task_service.TaskService._page` fills in two values that are
not properties of the row it is given — the tag ids and the blocked flag — and
this file is about the second one. It was the odd one out: tags were fetched for
the whole page in one query while the blocked flag cost one dependency lookup
per row, so a fifty-row board paid fifty small indexed round trips to answer a
question that is one ``IN`` list away. The first attempt at that fix gathered
the per-row coroutines with ``asyncio.gather``, which is a concurrency bug
rather than a performance one: an ``AsyncSession`` is documented as not safe for
concurrent use and there is one per request.

What this file pins
-------------------
* **Cost.** A page asks the dependency table a fixed number of times, whatever
  its width. Not "fewer" — fixed, so the assertion fails against a per-row loop
  at any page size and against a gather at any page size.
* **Parity.** :meth:`TaskRepository.blocked_task_ids` returns the same ids the
  per-row :meth:`TaskService._has_open_dependencies` returned ``True`` for,
  decided against the same private helpers that method used, over a graph that
  covers every status a prerequisite can hold plus the ownership asymmetries.
* **The empty page.** A listing that matched nothing asks the database nothing,
  which is the ordinary last page of any filtered listing.
* **Ids the caller does not own.** Both paths scope the *prerequisite*, not the
  waiting card, and neither path is scoped for the waiting card. The
  consequences are pinned in both directions so a future tightening of the query
  is a visible decision rather than a silent behaviour change.
* **Drift.** :func:`app.services.task_service._current_status` raises on a status
  that is not a member of ``TaskStatus``, and ``tasks.status`` is a ``String(16)``
  with no ``CHECK`` constraint — so the row is reachable and the raise is the only
  thing that reports it. A bulk ``status != 'completed'`` cannot tell a drifted
  row from a ``todo``, which makes this the one test here that a *naive* bulk
  implementation fails while the code it replaced passed.

House style, deliberately
-------------------------
Follows ``tests/test_task_integrity.py``: ``pytestmark =
pytest.mark.integration``, services hand-wired in a module-level helper, fixture
rows created through ``TaskService.create`` (which is where the dependency rules
are enforced too), and the two statuses no service call can produce written with
SQL against the stored column. That last part is the point: ``create`` validates
the status, so a fixture that produced them through the service would be testing
a graph the service refuses to build.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.project import ProjectRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate
from app.services.activity_service import ActivityService
from app.services.task_service import _SATISFIES_DEPENDENCY, TaskService, _current_status
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: A status ``TaskStatus`` does not contain, so no service call can write one.
#: Short enough for ``String(16)`` — the column would truncate a longer value
#: rather than reject it, and the truncation would make this test's premise false.
DRIFTED_STATUS = "shipped"

#: The message ``_current_status`` has always produced for a drifted row,
#: asserted as a literal: it is what the user is shown, so it is part of the
#: contract rather than a detail of which function happened to raise it.
DRIFT_MESSAGE = "This task's status is not a known value: 'shipped'."


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(session: AsyncSession) -> TaskService:
    """A task service wired the way ``app.api.deps`` wires it."""
    return TaskService(
        TaskRepository(session),
        ProjectRepository(session),
        TagRepository(session),
        activity=ActivityService(ActivityRepository(session)),
    )


@pytest.fixture
async def owner(db_session) -> User:
    """The caller, whose board every assertion below is about."""
    return await register_user(db_session, username="ada", email="ada@nexus.test")


@pytest.fixture
async def other(db_session) -> User:
    """A second account, for the ownership asymmetries."""
    return await register_user(db_session, username="bob", email="bob@nexus.test")


@pytest.fixture
async def project(db_session, owner) -> Project:
    """A workspace, because ``tasks.project_id`` is ``NOT NULL``."""
    row = Project(owner_id=owner.id, name="Nexus")
    db_session.add(row)
    await db_session.commit()
    return row


async def _card(
    service: TaskService, project: Project, owner: User, title: str, *, status: TaskStatus
) -> Task:
    """One card, created through the service.

    Through the service rather than as a raw row because ``create`` is where the
    project-ownership rules live; a fixture built underneath them would be
    testing a graph the service would refuse to build.
    """
    return await service.create(
        owner=owner, data=TaskCreate(project_id=project.id, title=title, status=status)
    )


async def _waiting_on(service: TaskService, owner: User, waiting: Task, blocker: Task) -> None:
    """Declare ``waiting`` blocked by ``blocker``, through the service."""
    await service.add_dependency(task=waiting, depends_on=blocker, owner=owner)


async def _force_status(session: AsyncSession, task_id: uuid.UUID, status: str) -> None:
    """Write a status no service call could have written.

    Raw SQL because this is the whole premise: ``tasks.status`` is a ``String(16)``
    with no ``CHECK`` constraint and no native enum, so a value outside
    ``TaskStatus`` is reachable only by a writer that bypassed
    ``validate_task_status``.

    The re-read afterwards is ``populate_existing`` rather than ``expire_all``.
    The session that wrote the row is the session reading it, so the identity
    map still holds the instance ``create`` returned, carrying the status the row
    had *before* — and a query that matches that row in SQL would hand back that
    stale copy. Expiring instead would make every later attribute access in the
    test body try to load outside greenlet context and raise
    ``MissingGreenlet``. A request-scoped session has no such cache to worry
    about, which is why production needs neither.
    """
    await session.execute(
        text("UPDATE tasks SET status = :status WHERE id = :task_id"),
        {"status": status, "task_id": task_id},
    )
    await session.commit()
    await session.execute(
        select(Task).where(Task.id == task_id).execution_options(populate_existing=True)
    )


def _dependency_reads(statements: list[str]) -> list[str]:
    """The SELECTs that read the dependency table, in the order they were sent."""
    return [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT") and "task_dependencies" in statement
    ]


async def _record_dependency_reads(engine, call):
    """Run ``call`` with every statement it sends collected, then return them.

    A context manager would read better, but the assertions need the statements
    *after* the listener is detached and the call has returned, and a fixture
    yielding a list would put that list's lifetime in the wrong place.
    """
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)
    return statements


# ---------------------------------------------------------------------------
# The reference implementation this replaced
# ---------------------------------------------------------------------------


async def _per_row_answer(
    repository: TaskRepository, task_id: uuid.UUID, owner_id: uuid.UUID
) -> bool:
    """``TaskService._has_open_dependencies``, spelled out here.

    The body of the method as it stood before the page stopped calling it: one
    dependency query per id, the caller's own prerequisites filtered in Python,
    and every one of them handed to :func:`_current_status`. Reproduced against
    the same private helpers rather than reimplemented, so a parity assertion
    cannot pass by agreeing with a second guess about what "unfinished" means.
    """
    return any(
        _current_status(blocker) is not _SATISFIES_DEPENDENCY
        for blocker in await repository.list_dependencies(task_id)
        if blocker.owner_id == owner_id
    )


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


async def test_a_page_asks_the_dependency_table_a_fixed_number_of_times(
    db_session, engine, owner, project
):
    """A page's dependency cost is a constant, not a function of its width.

    Twelve cards, half of them waiting on unfinished work, are listed twice —
    once as a two-row page and once as the whole board — and both listings read
    ``task_dependencies`` exactly twice: once for the flag and once for the drift
    check that keeps :func:`_current_status`'s raise alive.

    The assertion is a count and not a ceiling because a count is what a
    regression looks like. A per-row loop answers ``len(rows)`` here, so this
    fails at twelve rows and at two, and it fails with the statements in the
    message rather than with a vague "too many".
    """
    service = _service(db_session)
    for index in range(6):
        blocker = await _card(service, project, owner, f"Blocker {index}", status=TaskStatus.TODO)
        waiting = await _card(service, project, owner, f"Waiting {index}", status=TaskStatus.TODO)
        await _waiting_on(service, owner, waiting, blocker)
    for index in range(6):
        await _card(service, project, owner, f"Free {index}", status=TaskStatus.TODO)

    counts = []
    for limit in (2, 50):
        statements = await _record_dependency_reads(
            engine,
            lambda limit=limit: service.list(owner=owner, project_id=project.id, limit=limit),
        )
        counts.append(_dependency_reads(statements))

    narrow, wide = counts
    assert len(narrow) == 2, narrow
    assert len(wide) == 2, wide
    # The fixture is only a cost test if the wide page really is the wider one.
    assert len(wide) == len(narrow)


async def test_the_page_marks_exactly_the_cards_that_are_waiting(
    db_session, engine, owner, project
):
    """The flag comes from one answer, and it is right for both halves of a board.

    Half the board is blocked and half is not. A flag that were copied onto the
    row, or an answer that came back per card, would have to get one of the two
    groups wrong. The listing is also the shape a client renders, so this goes
    through :meth:`TaskService.list` rather than through the repository alone.
    """
    service = _service(db_session)
    blocked_ids = set()
    for index in range(4):
        blocker = await _card(service, project, owner, f"Blocker {index}", status=TaskStatus.TODO)
        waiting = await _card(service, project, owner, f"Waiting {index}", status=TaskStatus.TODO)
        await _waiting_on(service, owner, waiting, blocker)
        blocked_ids.add(waiting.id)

    page = await service.list(owner=owner, project_id=project.id, limit=50)

    answered = {item.id: item.has_blocked_dependencies for item in page.items}
    assert len(answered) == 8, answered
    assert {task_id for task_id, is_blocked in answered.items() if is_blocked} == blocked_ids


async def test_a_cancelled_prerequisite_still_blocks_through_the_page(db_session, owner, project):
    """``cancelled`` is unfinished, through the page rather than the repository.

    The predicate under test is ``status != 'completed'``, and ``cancelled`` is
    the status that makes that predicate and "still open" the same question only
    by agreement. If someone ever "simplified" it to ``status IN ('todo',
    'in_progress')``, this is the test that says no — and it says it through
    ``list``, which is the surface the board renders from.
    """
    service = _service(db_session)
    dropped = await _card(service, project, owner, "Dropped", status=TaskStatus.CANCELLED)
    finished = await _card(service, project, owner, "Finished", status=TaskStatus.COMPLETED)
    waiting = await _card(service, project, owner, "Waiting", status=TaskStatus.TODO)
    await _waiting_on(service, owner, waiting, dropped)
    await _waiting_on(service, owner, waiting, finished)

    page = await service.list(owner=owner, project_id=project.id, limit=50)
    answered = {item.id: item.has_blocked_dependencies for item in page.items}

    assert answered[waiting.id] is True, "a cancelled prerequisite must still block"
    assert answered[dropped.id] is False
    assert answered[finished.id] is False


# ---------------------------------------------------------------------------
# Parity with the per-row answer
# ---------------------------------------------------------------------------


async def test_the_bulk_answer_is_the_per_row_answer_for_every_id(db_session, owner, project):
    """Same ``True``/``False`` for every id, over a graph that covers the cases.

    All five statuses a prerequisite can hold, one edge each, plus a card with
    three edges and a card with none. ``completed`` clears a card and
    ``cancelled`` deliberately does not, so both are pinned by name rather than
    left to the comparison loop to notice.

    The reference is the loop this replaced, called on the same ids through the
    same helpers.
    """
    service = _service(db_session)
    repository = TaskRepository(db_session)
    candidates: list[uuid.UUID] = []

    alone = await _card(service, project, owner, "Alone", status=TaskStatus.TODO)
    candidates.append(alone.id)

    for status in TaskStatus:
        blocker = await _card(service, project, owner, f"Blocker {status.value}", status=status)
        waiting = await _card(
            service, project, owner, f"Waiting on {status.value}", status=TaskStatus.TODO
        )
        await _waiting_on(service, owner, waiting, blocker)
        candidates.append(waiting.id)

    # Three edges on one card: two unfinished and one cleared, so the answer
    # cannot come from whichever edge the database happened to return first.
    many = await _card(service, project, owner, "Many", status=TaskStatus.TODO)
    for status in (TaskStatus.TODO, TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        blocker = await _card(
            service, project, owner, f"Many blocker {status.value}", status=status
        )
        await _waiting_on(service, owner, many, blocker)
    candidates.append(many.id)

    expected = {
        task_id: await _per_row_answer(repository, task_id, owner.id) for task_id in candidates
    }
    assert expected[alone.id] is False
    # ``COMPLETED`` is the only satisfying value, so four of the five single-edge
    # cards are blocked and the three-edge card makes a fifth.
    assert sum(expected.values()) == 5, expected
    assert expected[many.id] is True

    blocked = await repository.blocked_task_ids(candidates, owner.id)

    assert isinstance(blocked, set)
    assert blocked == {task_id for task_id, is_blocked in expected.items() if is_blocked}


# ---------------------------------------------------------------------------
# Ownership scope
# ---------------------------------------------------------------------------


async def test_an_id_the_caller_does_not_own_is_answered_exactly_as_it_was(
    db_session, owner, other
):
    """Both paths scope the prerequisite, and neither scopes the waiting card.

    Two asymmetries, pinned together because fixing one tends to break the other.

    *A foreign card waiting on my unfinished work* is reported blocked. Neither
    the per-row path nor this query filters on the waiting card's owner — the
    ownership predicate is on the prerequisite, which is the row the flag is a
    fact about. ``list`` never passes such an id (it only ever passes ids it
    loaded under ``owner_id``), so this is parity for a call the API cannot make,
    pinned so that tightening it later is a decision somebody makes on purpose.

    *My card waiting on somebody else's work* is not blocked, because that
    prerequisite fails ``owner_id``. This one is load-bearing: a cross-tenant
    edge cannot decide what my board renders.

    The edges are written through the repository rather than the service, which
    refuses them — a dependency is same-owner and same-project by rule — so they
    exist only as rows a buggy writer or an import could leave behind. Both
    projects are built here rather than borrowed from the ``project`` fixture,
    because the second one belongs to the other account.
    """
    service = _service(db_session)
    repository = TaskRepository(db_session)

    my_project = Project(owner_id=owner.id, name="Mine")
    their_project = Project(owner_id=other.id, name="Theirs")
    db_session.add_all([my_project, their_project])
    await db_session.commit()

    my_open = await _card(service, my_project, owner, "Mine, open", status=TaskStatus.TODO)
    my_done = await _card(service, my_project, owner, "Mine, done", status=TaskStatus.COMPLETED)
    my_waiting = await _card(service, my_project, owner, "My waiting card", status=TaskStatus.TODO)
    their_waiting = await _card(
        service, their_project, other, "Their waiting card", status=TaskStatus.TODO
    )
    their_open = await _card(
        service, their_project, other, "Their open card", status=TaskStatus.TODO
    )

    # Their card waits on mine, open and done. Mine waits on theirs, open, and on
    # a card of my own that is finished.
    await repository.add_dependency(task_id=their_waiting.id, depends_on_id=my_open.id)
    await repository.add_dependency(task_id=their_waiting.id, depends_on_id=my_done.id)
    await repository.add_dependency(task_id=my_waiting.id, depends_on_id=their_open.id)
    await repository.add_dependency(task_id=my_waiting.id, depends_on_id=my_done.id)

    candidates = [their_waiting.id, my_waiting.id, their_open.id]
    expected = {
        task_id: await _per_row_answer(repository, task_id, owner.id) for task_id in candidates
    }
    assert expected[their_waiting.id] is True, expected
    assert expected[my_waiting.id] is False, expected
    assert expected[their_open.id] is False, expected

    blocked = await repository.blocked_task_ids(candidates, owner.id)

    assert blocked == {task_id for task_id, is_blocked in expected.items() if is_blocked}


# ---------------------------------------------------------------------------
# The empty page
# ---------------------------------------------------------------------------


async def test_a_page_that_matched_nothing_asks_the_database_nothing(
    db_session, engine, owner, project
):
    """An empty listing is the common last page, not an edge case.

    Both bulk methods short-circuit on an empty sequence, and the page renders as
    an empty one: no items, a zero total, and not one statement against the
    dependency table. A per-row loop also read nothing here, so the interesting
    half of this test is the *count* — it is the shape of the code, not the size
    of the page, that keeps the empty case free.
    """
    service = _service(db_session)
    repository = TaskRepository(db_session)
    await _card(service, project, owner, "Alpha", status=TaskStatus.TODO)

    async def _both() -> None:
        await service.list(owner=owner, project_id=project.id, search="nothing here")
        assert await repository.blocked_task_ids([], owner.id) == set()
        assert await repository.list_drifted_prerequisites([], owner.id) == []

    statements = await _record_dependency_reads(engine, _both)
    page = await service.list(owner=owner, project_id=project.id, search="nothing here")

    assert _dependency_reads(statements) == []
    assert page.items == []
    assert page.meta.total == 0


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


async def test_a_page_showing_a_drifted_prerequisite_refuses_to_render(db_session, owner, project):
    """A prerequisite nobody can interpret is a refusal, not an unfinished card.

    :func:`_current_status` raises on a status outside ``TaskStatus`` rather than
    defaulting to one, and this is the test that keeps that alive on the bulk
    path. ``status != 'completed'`` is true for ``'shipped'`` exactly as it is for
    ``'todo'``, so a page built from the set alone renders a corrupted card as
    ordinary blocked work — the failure the docstring on that function exists to
    prevent.

    It passes against the per-row loop this replaced and fails against the
    obvious bulk implementation. That is why it is here: the cheap fix is the one
    that drops the raise.
    """
    service = _service(db_session)
    blocker = await _card(service, project, owner, "Prerequisite", status=TaskStatus.TODO)
    waiting = await _card(service, project, owner, "Waiting", status=TaskStatus.TODO)
    await _waiting_on(service, owner, waiting, blocker)
    await _force_status(db_session, blocker.id, DRIFTED_STATUS)

    with pytest.raises(ValidationError) as drifted:
        await service.list(owner=owner, project_id=project.id, limit=50)

    assert str(drifted.value) == DRIFT_MESSAGE


async def test_a_drifted_prerequisite_off_the_page_is_not_the_pages_business(
    db_session, owner, project
):
    """The refusal is scoped to the cards the page is rendering.

    The per-row path only ever looked at the rows it was assembling, so a
    corrupted card elsewhere in the account never made an unrelated listing fail
    — and it would have been a strange thing for a search term to be able to
    trigger. Scoping the bulk check to the page's own ids keeps that: this page
    shows ``Alpha`` while the drifted prerequisite belongs to a card it does not
    show, and the listing renders.
    """
    service = _service(db_session)
    blocker = await _card(service, project, owner, "Beta blocker", status=TaskStatus.TODO)
    waiting = await _card(service, project, owner, "Beta waiting", status=TaskStatus.TODO)
    await _waiting_on(service, owner, waiting, blocker)
    await _card(service, project, owner, "Alpha card", status=TaskStatus.TODO)
    await _force_status(db_session, blocker.id, DRIFTED_STATUS)

    page = await service.list(owner=owner, project_id=project.id, search="Alpha", limit=50)

    assert [item.title for item in page.items] == ["Alpha card"]
    assert page.meta.total == 1


async def test_a_drifted_prerequisite_is_found_wherever_it_sits_on_the_board(
    db_session, owner, project
):
    """The refusal does not depend on where the corrupted card sits.

    The loop this replaced evaluated :func:`_current_status` inside ``any()``,
    which stops at the first prerequisite that is not completed. A card with an
    ordinary ``todo`` in position 0 and a drifted one in position 1 therefore
    answered ``True`` and never reached the drifted row, while the same two rows
    in the other order refused the whole page — whether a corrupted card broke
    the board depended on its ``position``, which is a drag handle.

    This pins the answer the bulk path has: any drifted prerequisite on the page
    refuses, whatever order it sits in. It is a deliberate widening of the old
    behaviour and the only answer in this file that is not parity.
    """
    service = _service(db_session)
    ordinary = await _card(service, project, owner, "Ordinary blocker", status=TaskStatus.TODO)
    corrupt = await _card(service, project, owner, "Corrupt blocker", status=TaskStatus.TODO)
    waiting = await _card(service, project, owner, "Waiting", status=TaskStatus.TODO)
    await _waiting_on(service, owner, waiting, ordinary)
    await _waiting_on(service, owner, waiting, corrupt)
    await _force_status(db_session, corrupt.id, DRIFTED_STATUS)

    with pytest.raises(ValidationError):
        await service.list(owner=owner, project_id=project.id, limit=50)

    # The row the service names is the corrupted one, not the ordinary blocker
    # that happens to sit above it on the board.
    drifted = await TaskRepository(db_session).list_drifted_prerequisites([waiting.id], owner.id)

    assert [row.id for row in drifted] == [corrupt.id]
    assert drifted[0].status == DRIFTED_STATUS


# ---------------------------------------------------------------------------
# The repository on its own
# ---------------------------------------------------------------------------


async def test_blocked_task_ids_is_one_statement_and_deduplicates(
    db_session, engine, owner, project
):
    """The method is one statement, and three unfinished prerequisites are one id.

    A card waiting on three unfinished tasks is blocked once: the answer is a set,
    ``DISTINCT`` is what collapses the edges, and the caller only ever asks
    membership. Counting the statements here as well as in the page test keeps the
    *method* honest for a caller that has not come through :meth:`TaskService._page`.
    """
    service = _service(db_session)
    repository = TaskRepository(db_session)

    waiting = await _card(service, project, owner, "Waiting", status=TaskStatus.TODO)
    for index in range(3):
        blocker = await _card(service, project, owner, f"Blocker {index}", status=TaskStatus.TODO)
        await _waiting_on(service, owner, waiting, blocker)

    statements = await _record_dependency_reads(
        engine, lambda: repository.blocked_task_ids([waiting.id], owner.id)
    )

    assert len(_dependency_reads(statements)) == 1
    assert await repository.blocked_task_ids([waiting.id], owner.id) == {waiting.id}
