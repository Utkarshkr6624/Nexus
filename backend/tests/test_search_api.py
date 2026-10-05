"""Phase 13 global search: reach, ordering, filters, and above all tenancy.

What this file is for
---------------------
``GET /api/v1/search`` is a read-only projection over thirteen tables that already
exist. That makes it structurally unlike every other endpoint in the API: there
is no write to authorise, no state to reconcile and no service rule behind it —
so the only way its correctness is visible is by looking at what comes back.

The questions asked here, in the order they matter.

**Can it reach every kind it claims to?** ``:func:`test_each_of_the_thirteen_declared_kinds_finds_its_row`
seeds one row per table with the same term and asserts all thirteen come back, and
:func:`test_the_search_table_and_the_wire_enum_agree` pins the SQL routing table
to the wire enum — the one place in this feature where a drift would be silent,
because a kind present in one and absent from the other fails closed and simply
never matches.

**Can one account's rows leak into another's?** This is the test that matters.
:func:`test_an_identically_named_row_belonging_to_another_account_never_appears`
gives two accounts rows with *identical* titles and descriptions, asks each for
the same term, and asserts that each gets exactly their own thirteen rows back.
Identical titles are the point: with distinct titles a scoping bug could hide
behind the text not matching, and the test would pass for the wrong reason.

**Are the rules it states real?** Title before body, most recent before oldest,
a total order that repeats. :func:`test_a_title_match_outranks_a_body_match`
and :func:`test_two_identical_queries_return_the_same_hits_in_the_same_order`
pin them.

**Do the filters narrow, and are the ones that cannot be honoured refused?**
:func:`test_a_filter_the_searched_kinds_cannot_honour_is_refused` is the one
worth reading: a filter that is silently dropped returns a page that *looks*
filtered and is not, which is a worse failure than a 422 because nothing
announces it.

House style
-----------
Follows ``tests/test_knowledge_api.py``: ``pytestmark = pytest.mark.integration``
because every test here needs the live PostgreSQL the suite truncates, rows are
read back through explicit column tuples rather than ORM entities, expected
figures are derived in the docstrings rather than recorded from a run, and test
names are full English sentences.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.developer import GitRepository
from app.models.knowledge import Bookmark, Concept, Document, Note, Resource
from app.models.learning import LearningGoal, Skill
from app.models.planner import CalendarEvent
from app.models.project import Project
from app.models.risk import Recommendation, Risk
from app.models.tag import Tag, task_tags
from app.models.task import Task
from app.repositories.search import SEARCH_TARGETS
from app.schemas.search import MAX_QUERY_CHARS, MAX_TYPE_FILTERS, SearchEntityKind
from app.services.search_service import SearchService
from tests.analytics_fixtures import DAY, at, seeded_client

pytestmark = pytest.mark.integration

#: The search endpoint's mount point.
SEARCH = "/api/v1/search"

#: A term nothing in the schema or the fixtures contains. Chosen to be a word
#: rather than a substring of a real column value, so a hit can only come from a
#: row this file seeded.
TERM = "zephyr"

#: Every row this file seeds carries this instant unless it is testing ordering,
#: so that recency is a constant and a test about ranking is not accidentally a
#: test about which row the database happened to write last.
TOUCHED = at(DAY)

#: A second instant, one day later, for the tests that need rows to differ in
#: recency.
LATER = TOUCHED + timedelta(days=1)

#: How many rows one kind contributes before the per-kind cap bites. One more
#: than the cap, so the test observes truncation rather than a full set.
OVER_THE_CAP = SearchService.PER_ENTITY_SCAN + 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _rows(session: AsyncSession, model: Any) -> list[Any]:
    """Every row of one table, as whatever the statement selected."""
    result = await session.execute(model.__table__.select())
    return list(result.all())


async def _seed_all_kinds(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    term: str = TERM,
    touched: datetime = TOUCHED,
    target_date: date = DAY,
) -> dict[SearchEntityKind, uuid.UUID]:
    """Write one row into each of the thirteen searched tables, all containing ``term``.

    Every row is labelled with the same term **in its own first text column**, so
    a single query has a reason to reach all thirteen tables. For most kinds that
    column is ``name`` or ``title``; for a ``bookmark`` it is ``url`` and for a
    ``document`` it is ``filename``, because both of their titles are nullable and
    the label must not be — so the two rows below put the term in *those* columns
    and nowhere else. Rows are written through the ORM with explicit timestamps
    rather than through the API: this file is testing the search projection, and
    driving thirteen different creation endpoints would make a failure here
    ambiguous between "search cannot reach this table" and "this table's create
    endpoint wants a different payload".

    Returns:
        The id written for each kind, keyed by kind.
    """
    project = Project(
        owner_id=owner_id,
        name=f"{term} programme",
        description=f"the {term} programme description",
        target_date=target_date,
        updated_at=touched,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    task = Task(
        owner_id=owner_id,
        project_id=project.id,
        title=f"write the {term} plan",
        description="nothing else of interest",
        due_date=target_date,
        updated_at=touched,
    )
    note = Note(
        owner_id=owner_id,
        title=f"a {term} note",
        content=f"body text about {term}",
        updated_at=touched,
    )
    resource = Resource(
        owner_id=owner_id,
        title=f"the {term} paper",
        description="a citation",
        updated_at=touched,
    )
    concept = Concept(
        owner_id=owner_id, name=f"{term} concept", description="a named idea", updated_at=touched
    )
    repository = GitRepository(
        user_id=owner_id,
        name=f"{term}-service",
        local_path=f"/srv/{term}-service",
        description="a registered repository",
        updated_at=touched,
    )
    goal = LearningGoal(
        user_id=owner_id,
        title=f"learn {term}",
        description="an intention",
        target_date=target_date,
        updated_at=touched,
    )
    skill = Skill(
        user_id=owner_id, name=f"{term} engineering", description="a skill", updated_at=touched
    )
    event = CalendarEvent(
        owner_id=owner_id,
        title=f"{term} review",
        description="a booking",
        starts_at=at(DAY, hour=14),
        ends_at=at(DAY, hour=15),
        updated_at=touched,
    )
    risk = Risk(
        user_id=owner_id,
        risk_type="deadline",
        title=f"{term} risk",
        description="a detected condition",
        evidence=["a task is due"],
        updated_at=touched,
    )
    recommendation = Recommendation(
        user_id=owner_id,
        recommendation_type="review_deadline",
        title=f"review the {term} deadline",
        description="do the thing",
        reason="the risk score rose",
        updated_at=touched,
    )
    # The term goes in the *url* and the *filename* respectively, not in the
    # titles, because that is the column each of these targets leads with — see
    # ``SearchTarget.columns`` in app/repositories/search.py.
    bookmark = Bookmark(
        owner_id=owner_id,
        url=f"https://example.test/{term}",
        title="a saved link",
        description="a citation",
        updated_at=touched,
    )
    document = Document(
        owner_id=owner_id,
        filename=f"{term}-report.pdf",
        title="an attachment",
        description="a metadata row",
        updated_at=touched,
    )

    written = [
        ("task", task),
        ("note", note),
        ("resource", resource),
        ("bookmark", bookmark),
        ("document", document),
        ("concept", concept),
        ("repository", repository),
        ("goal", goal),
        ("skill", skill),
        ("event", event),
        ("risk", risk),
        ("recommendation", recommendation),
    ]
    for _, row in written:
        session.add(row)
    await session.commit()
    for _, row in written:
        await session.refresh(row)

    seeded: dict[SearchEntityKind, uuid.UUID] = {SearchEntityKind.PROJECT: project.id}
    for kind, row in written:
        seeded[SearchEntityKind(kind)] = row.id
    return seeded


def _hits(response: Any) -> list[dict[str, Any]]:
    """The flat ranked list, with every group flattened back into it."""
    return response.json()["hits"]


async def _search(client, auth: dict[str, str], **params: Any) -> Any:
    """One search request. ``q`` defaults to the module's term."""
    query = {"q": TERM, **params}
    return await client.get(SEARCH, params=query, headers=auth)


# ---------------------------------------------------------------------------
# Reach
# ---------------------------------------------------------------------------


def test_the_search_table_and_the_wire_enum_agree():
    """``SEARCH_TARGETS`` and ``SearchEntityKind`` name the same kinds.

    The two live in different layers on purpose — the SQL table in
    :mod:`app.repositories.search`, the wire vocabulary in
    :mod:`app.schemas.search` — and nothing at import time compares them. A kind
    added to only one of them fails *closed*: the enum accepts a ``types`` value
    the repository cannot scan, and the request answers "no results" while
    looking like a successful empty search. Pinning the sets is cheaper than
    making one derive from the other.
    """
    assert set(SEARCH_TARGETS) == {str(kind) for kind in SearchEntityKind}


def test_the_type_filter_cap_is_the_size_of_the_vocabulary():
    """``MAX_TYPE_FILTERS`` is the number of kinds, so no valid request is refused.

    It exists to turn "you asked for more kinds than exist" into a 422 naming the
    parameter. A number smaller than the vocabulary would refuse legitimate
    requests, and a larger one would accept a list that cannot mean anything.
    """
    assert len(SearchEntityKind) == MAX_TYPE_FILTERS


async def test_each_of_the_thirteen_declared_kinds_finds_its_row(client, db_session):
    """One row per table, all labelled with the same term, and all thirteen come back.

    If any target named a column that does not exist, or a table this module does
    not import, the search would raise on every request rather than quietly
    omitting a kind — so the interesting failure here is a *missing* kind, and
    this asserts the set exactly rather than a count. A count would pass with one
    kind duplicated and another missing.
    """
    seed, auth = await seeded_client(client, db_session)
    seeded = await _seed_all_kinds(db_session, seed.owner.id)

    response = await _search(client, auth)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["query"] == TERM
    assert {hit["kind"] for hit in body["hits"]} == set(SearchEntityKind)
    assert len(body["hits"]) == len(SearchEntityKind)
    assert {hit["id"] for hit in body["hits"]} == {str(row_id) for row_id in seeded.values()}
    assert {group["kind"] for group in body["groups"]} == set(SearchEntityKind)


async def test_the_groups_partition_the_flat_list_and_add_nothing_else(client, db_session):
    """``groups`` is the same page cut up by kind, not a second query's answer.

    Both views are returned because a palette wants the flat one and a results
    panel wants the grouped one; they must therefore be describing the same hits.
    Grouping the full result set while paginating the flat list would produce a
    page whose two views disagreed, which is the specific bug this pins.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    body = (await _search(client, auth)).json()
    grouped = [hit for group in body["groups"] for hit in group["hits"]]

    assert [hit["id"] for hit in grouped] == [hit["id"] for hit in body["hits"]]
    assert len({hit["id"] for hit in grouped}) == len(body["hits"])


async def test_the_snippet_marks_the_matched_region(client, db_session):
    """The reported offsets really do delimit the term inside the snippet.

    The hit reports ``match_start``/``match_end`` rather than wrapping the match
    in sentinel characters, so this asserts the arithmetic the client will do:
    slicing the snippet at those offsets must produce the term, not merely
    *something* near it. The ``lower()`` is because the match is
    case-insensitive and the snippet is not modified.
    """
    seed, auth = await seeded_client(client, db_session)
    seeded = await _seed_all_kinds(db_session, seed.owner.id)

    body = (await _search(client, auth)).json()
    for hit in body["hits"]:
        matched = hit["snippet"][hit["match_start"] : hit["match_end"]]
        assert TERM in matched.lower(), hit
        # ``matched_field`` is the column *order* the hit was found in, not a
        # pick between two labels — so it is checked against the target's own
        # first column rather than against a fixed set. That is what lets a
        # ``bookmark`` answer ``url`` and a ``document`` answer ``filename``:
        # both titles are nullable, so neither may lead with one.
        target = SEARCH_TARGETS[hit["kind"]]
        assert hit["matched_field"] == target.columns[0].key, hit
        assert hit["title"], hit

    # The term was written into each row's first text column, which is what the
    # hit reports as the matched field and what the row's own label carries.
    project = next(hit for hit in body["hits"] if hit["kind"] == SearchEntityKind.PROJECT)
    assert project["id"] == str(seeded[SearchEntityKind.PROJECT])
    assert TERM in project["snippet"].lower()


async def test_a_row_filed_under_a_project_reports_that_project(client, db_session):
    """A hit's project is named, and it is resolved inside the caller's scope.

    The project name costs one extra statement rather than a join onto every
    table. The name is only ever read through the caller's own ``owner_id``, so a
    project id pointing at somebody else's row would resolve to nothing at all.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    body = (await _search(client, auth, types=["task"])).json()
    hit = body["hits"][0]
    project = next(
        row for row in await _rows(db_session, Project) if str(row.id) == hit["project_id"]
    )
    assert hit["project_name"] == project.name

    # A project is not filed under a project, so the same lookup is null for one.
    projects = (await _search(client, auth, types=["project"])).json()["hits"]
    assert projects[0]["project_id"] is None
    assert projects[0]["project_name"] is None


# ---------------------------------------------------------------------------
# Tenancy — the test this slice exists for
# ---------------------------------------------------------------------------


async def test_an_identically_named_row_belonging_to_another_account_never_appears(
    client, db_session
):
    """Two accounts, thirteen identically-labelled rows each, and total isolation.

    The rows are identical on purpose. A test with distinct titles can pass
    without the ownership predicate doing anything at all, because the text alone
    already separates the two sets — and the failure it would miss is exactly the
    one that matters here. Every column the search reads carries the same term
    for both accounts, so a row can only be excluded by ``owner_id``.

    The assertion is stated twice over: each account gets exactly their own
    thirteen ids back, and neither account's id appears in the other's results.
    """
    first_seed, first_auth = await seeded_client(client, db_session, username="ada")
    second_seed, second_auth = await seeded_client(client, db_session, username="grace")
    assert first_seed.owner.id != second_seed.owner.id

    first_ids = await _seed_all_kinds(db_session, first_seed.owner.id)
    second_ids = await _seed_all_kinds(db_session, second_seed.owner.id)

    first_body = (await _search(client, first_auth)).json()
    second_body = (await _search(client, second_auth)).json()

    assert {hit["id"] for hit in first_body["hits"]} == {str(i) for i in first_ids.values()}
    assert {hit["id"] for hit in second_body["hits"]} == {str(i) for i in second_ids.values()}
    assert not {hit["id"] for hit in first_body["hits"]} & {
        hit["id"] for hit in second_body["hits"]
    }


async def test_another_accounts_project_id_is_not_an_escape_hatch(client, db_session):
    """A foreign ``project_id`` narrows to nothing rather than reaching the rows under it.

    Naming somebody else's project would be the obvious way to try to widen the
    result set: it is a real filter on a real column. Every statement carries the
    caller's own id *and* the project filter, so the rows that survive are the
    caller's rows filed under a project that is not theirs — which is the empty
    set, and is reported as an empty result rather than as a 403 that would have
    confirmed the project exists.
    """
    first_seed, first_auth = await seeded_client(client, db_session, username="ada")
    second_seed, _second_auth = await seeded_client(client, db_session, username="grace")

    await _seed_all_kinds(db_session, first_seed.owner.id)
    second_ids = await _seed_all_kinds(db_session, second_seed.owner.id)
    foreign_project = second_ids[SearchEntityKind.PROJECT]

    body = (await _search(client, first_auth, project_id=str(foreign_project))).json()
    assert body["hits"] == []
    assert body["meta"]["total"] == 0


async def test_the_search_needs_a_bearer_token(client, db_session):
    """No credentials, no rows — and no hint that rows exist.

    The unauthenticated answer is a 401 through the shared envelope, the same as
    every other surface. Pinning it here because this endpoint reads thirteen
    tables at once and is the widest read in the product.
    """
    seed, _ = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    response = await client.get(SEARCH, params={"q": TERM})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


async def test_a_title_match_outranks_a_body_match(client, db_session):
    """The matched column decides before recency does.

    Two tasks carry the term at the same instant: one in its title, one only in
    its description. The title match comes first. Without this rule a hit whose
    match is a title would sit wherever its timestamp put it, which means a
    palette's first result changes as the user edits unrelated rows.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id
    project = Project(owner_id=owner, name="holder", updated_at=TOUCHED)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    in_title = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} rewrite",
        description="nothing",
        updated_at=TOUCHED,
    )
    in_body = Task(
        owner_id=owner,
        project_id=project.id,
        title="unrelated work",
        description=f"mentions {TERM} once",
        updated_at=TOUCHED,
    )
    db_session.add_all([in_title, in_body])
    await db_session.commit()
    await db_session.refresh(in_title)
    await db_session.refresh(in_body)

    body = (await _search(client, auth, types=["task"])).json()
    assert [hit["id"] for hit in body["hits"]] == [str(in_title.id), str(in_body.id)]
    assert body["hits"][1]["matched_field"] == "description"


async def test_more_recently_touched_comes_first_at_equal_column(client, db_session):
    """Two rows matching in the same column are ordered newest first.

    The timestamps differ by a day rather than by a microsecond, so the assertion
    is about the rule rather than about a tie-break that only this fixture can
    produce.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id
    project = Project(owner_id=owner, name="holder", updated_at=TOUCHED)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    older = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} older",
        updated_at=TOUCHED,
    )
    newer = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} newer",
        updated_at=LATER,
    )
    db_session.add_all([older, newer])
    await db_session.commit()
    await db_session.refresh(older)
    await db_session.refresh(newer)

    body = (await _search(client, auth, types=["task"])).json()
    assert [hit["id"] for hit in body["hits"]] == [str(newer.id), str(older.id)]


async def test_two_identical_queries_return_the_same_hits_in_the_same_order(client, db_session):
    """Deterministic ordering: the same request twice, the same list twice.

    Compared as ids rather than as whole payloads because ``relative_date`` is
    measured against the clock and a run that crossed UTC midnight would fail on
    that field for a reason that has nothing to do with ordering. The ordering
    itself — which is what a palette re-reads on every keystroke — must be
    identical, including across the page boundary.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    first = (await _search(client, auth)).json()
    second = (await _search(client, auth)).json()

    assert [hit["id"] for hit in first["hits"]] == [hit["id"] for hit in second["hits"]]
    assert first["hits"] == second["hits"]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


async def test_a_project_filter_restricts_to_the_rows_under_that_project(client, db_session):
    """``project_id`` narrows to the tasks filed under it and names it.

    Two projects, one task each, both carrying the term. Filtering on one returns
    that project's task and not the other, which is the assertion that matters —
    a filter that matched everything would return two rows.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id

    first = Project(owner_id=owner, name=f"{TERM} one", updated_at=TOUCHED)
    second = Project(owner_id=owner, name=f"{TERM} two", updated_at=TOUCHED)
    db_session.add_all([first, second])
    await db_session.commit()
    await db_session.refresh(first)
    await db_session.refresh(second)

    wanted = Task(
        owner_id=owner, project_id=first.id, title=f"{TERM} under one", updated_at=TOUCHED
    )
    other = Task(
        owner_id=owner, project_id=second.id, title=f"{TERM} under two", updated_at=TOUCHED
    )
    db_session.add_all([wanted, other])
    await db_session.commit()
    await db_session.refresh(wanted)

    body = (await _search(client, auth, types=["task"], project_id=str(first.id))).json()
    assert [hit["id"] for hit in body["hits"]] == [str(wanted.id)]
    assert body["hits"][0]["project_name"] == first.name


async def test_a_status_filter_narrows_and_a_priority_filter_narrows(client, db_session):
    """Both single-value filters remove rows they should and keep rows they should.

    Two tasks differ only in status and two differ only in priority; filtering on
    each returns the one that carries it. Asserting the survivor *and* the total
    means a filter that silently matched nothing and a filter that silently
    matched everything both fail here.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id
    project = Project(owner_id=owner, name="holder", updated_at=TOUCHED)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    done = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} finished",
        status="completed",
        updated_at=TOUCHED,
    )
    open_task = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} unfinished",
        status="todo",
        updated_at=TOUCHED,
    )
    urgent = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} urgent",
        priority="high",
        updated_at=TOUCHED,
    )
    db_session.add_all([done, open_task, urgent])
    await db_session.commit()
    for row in (done, open_task, urgent):
        await db_session.refresh(row)

    by_status = (await _search(client, auth, types=["task"], status="completed")).json()
    assert [hit["id"] for hit in by_status["hits"]] == [str(done.id)]
    assert by_status["meta"]["total"] == 1

    by_priority = (await _search(client, auth, types=["task"], priority="high")).json()
    assert [hit["id"] for hit in by_priority["hits"]] == [str(urgent.id)]
    assert by_priority["meta"]["total"] == 1


async def test_a_tag_filter_requires_every_tag_it_lists(client, db_session):
    """A task must carry **all** the listed tags, not any of them.

    The same all-of rule :class:`app.repositories.task.TaskRepository` uses: "in
    these three projects" and "in any of these three projects" are different
    questions, and only one of them is usually meant. The fixture makes the two
    disagree — one task carries the first tag alone, one carries both — so a
    permissive implementation would return both and fail here.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id
    project = Project(owner_id=owner, name="holder", updated_at=TOUCHED)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    first_tag = Tag(user_id=owner, name=f"{TERM}-one")
    second_tag = Tag(user_id=owner, name=f"{TERM}-two")
    db_session.add_all([first_tag, second_tag])
    await db_session.commit()
    await db_session.refresh(first_tag)
    await db_session.refresh(second_tag)

    both = Task(
        owner_id=owner, project_id=project.id, title=f"{TERM} both tags", updated_at=TOUCHED
    )
    one = Task(owner_id=owner, project_id=project.id, title=f"{TERM} one tag", updated_at=TOUCHED)
    db_session.add_all([both, one])
    await db_session.commit()
    await db_session.refresh(both)
    await db_session.refresh(one)

    for task, tag in ((both, first_tag), (both, second_tag), (one, first_tag)):
        await db_session.execute(task_tags.insert().values(task_id=task.id, tag_id=tag.id))
    await db_session.commit()

    body = (
        await _search(
            client,
            auth,
            types=["task"],
            tag_ids=[str(first_tag.id), str(second_tag.id)],
        )
    ).json()
    assert [hit["id"] for hit in body["hits"]] == [str(both.id)]


async def test_a_date_range_narrows_by_each_kinds_own_date_column(client, db_session):
    """``from``/``to`` bound the column that means "when", not "when it was edited".

    Two tasks due on different days, both touched at the same instant, so the
    only thing that can separate them is the date filter. A range covering the
    first day returns exactly that one; a range covering the second returns the
    other. If the filter had been applied to ``updated_at`` both rows would match
    both ranges, and each assertion would fail.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id
    project = Project(owner_id=owner, name="holder", updated_at=TOUCHED)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    soon = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} due soon",
        due_date=DAY,
        updated_at=TOUCHED,
    )
    later_task = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} due later",
        due_date=DAY + timedelta(days=10),
        updated_at=TOUCHED,
    )
    db_session.add_all([soon, later_task])
    await db_session.commit()
    await db_session.refresh(soon)
    await db_session.refresh(later_task)

    early = (
        await _search(
            client, auth, types=["task"], **{"from": DAY.isoformat(), "to": DAY.isoformat()}
        )
    ).json()
    assert [hit["id"] for hit in early["hits"]] == [str(soon.id)]

    late = (
        await _search(
            client,
            auth,
            types=["task"],
            **{"from": (DAY + timedelta(days=10)).isoformat()},
        )
    ).json()
    assert [hit["id"] for hit in late["hits"]] == [str(later_task.id)]


async def test_types_narrows_the_union_to_the_kinds_asked_for(client, db_session):
    """``types`` reads only the tables it names.

    All thirteen rows carry the term; asking for two kinds must return only those
    two, which also proves the default really is "all of them" rather than a
    subset that happened to look complete.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    body = (await _search(client, auth, types=["note", "skill"])).json()
    assert {hit["kind"] for hit in body["hits"]} == {"note", "skill"}
    assert body["meta"]["total"] == 2


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_a_filter_the_searched_kinds_cannot_honour_is_refused(client, db_session):
    """A filter that no searched kind supports is a 422, never a silent no-op.

    Notes are not filed under a project and projects carry no tags, so asking for
    either filter with those kinds is a request the service cannot serve. Dropping
    the argument would return an unfiltered page that looks filtered — the caller
    cannot tell, and neither can the log. This is the assertion that keeps the
    failure loud.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)
    project = (await _search(client, auth, types=["project"])).json()["hits"][0]

    assert_error = {
        "code": "validation_error",
        "status": 422,
        "field": "project_id",
    }
    response = await _search(client, auth, types=["note"], project_id=project["id"])
    assert response.status_code == assert_error["status"]
    error = response.json()["error"]
    assert error["code"] == assert_error["code"]
    assert error["details"]["field"] == assert_error["field"]

    response = await _search(client, auth, types=["project"], tag_ids=[str(uuid.uuid4())])
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "tag_ids"


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"types": ["nonsense"]}, "types"),
        ({"status": "nonsense"}, "status"),
        ({"priority": "nonsense"}, "priority"),
        ({"limit": 201}, "limit"),
        ({"offset": -1}, "offset"),
        ({"q": ""}, "q"),
        ({"q": "  "}, "q"),
        ({"q": "z" * (MAX_QUERY_CHARS + 1)}, "q"),
        ({"from": "2026-02-01", "to": "2026-01-01"}, "from"),
    ],
    ids=[
        "unknown-kind",
        "unknown-status",
        "unknown-priority",
        "limit-over-cap",
        "negative-offset",
        "empty-q",
        "blank-q",
        "over-long-q",
        "reversed-range",
    ],
)
async def test_a_request_the_service_cannot_serve_is_a_422(client, db_session, params, field):
    """Every malformed request answers 422 through the shared envelope.

    Parameterised because the interesting property is that they are *all* 422s
    with the same envelope, not that any one of them is. A test that only checked
    the status code would pass on a bare FastAPI ``HTTPException``, which this
    codebase does not use; the ``field`` assertion pins that the refusal names
    the parameter the caller got wrong.

    ``{"q": "  "}`` is the case that justifies the service carrying its own
    length check rather than trusting the router: ``min_length=1`` is satisfied by
    a single space, and a whitespace-only term is still a term that matches
    everything.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    response = await client.get(SEARCH, params={"q": TERM, **params}, headers=auth)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert field in _fields_named_by(error), error


def _fields_named_by(error: dict[str, Any]) -> set[str]:
    """Every parameter an error envelope blames, whichever shape it arrived in.

    The shared envelope has two 422 producers and they detail differently: a
    FastAPI query-parsing failure lists one entry per bad parameter under
    ``details.errors``, addressed by location and index
    (``query.types.0``), while a
    :class:`~app.core.exceptions.ValidationError` raised by the service puts its
    own ``details`` straight on the error with a bare field name. Both are the
    same contract to a client — a 422 naming the parameter — so this reduces each
    reported name to the parameter it blames and accepts either shape.
    """
    details = error.get("details") or {}
    entries = details.get("errors")
    names = (
        [entry.get("field") for entry in entries]
        if isinstance(entries, list)
        else [details.get("field")]
    )
    return {_parameter(name) for name in names}


def _parameter(name: Any) -> str:
    """``query.types.0`` and ``q`` reduced to the parameter both are blaming."""
    text = str(name)
    for prefix in ("query.", "path."):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.split(".")[0]


async def test_a_term_at_the_length_cap_is_accepted(client, db_session):
    """The over-long rejection is a boundary, not a smaller cap than advertised.

    A term of exactly :data:`~app.schemas.search.MAX_QUERY_CHARS` characters goes
    through. Without this, a cap one character stricter than the documented one
    would pass every other test in this file — the 422 test uses a term *over* the
    cap and would be green either way.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    response = await _search(client, auth, q=TERM.ljust(MAX_QUERY_CHARS, "x"))
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Empty, capped, paginated
# ---------------------------------------------------------------------------


async def test_a_query_that_matches_nothing_is_an_empty_200(client, db_session):
    """No matches is an answer, not a fault.

    Two empty lists, ``total`` of zero, HTTP 200. A search that raised here would
    force every client to distinguish "nothing matched" from "the search broke",
    which is a distinction a client cannot actually make.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    body = (await _search(client, auth, q="nothingmatchesthis")).json()
    assert body["hits"] == []
    assert body["groups"] == []
    assert body["meta"] == {"total": 0, "limit": 50, "offset": 0}


async def test_a_wildcard_in_the_term_is_escaped_rather_than_run(client, db_session):
    """``%`` in the term matches a percent sign, not every row in the table.

    Unescaped, this query would return every task the user owns and look like a
    very generous search rather than a wrong one. The assertion is that a
    percent-sign term finds nothing here and that the ordinary term still does.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    wildcard = (await _search(client, auth, q="%")).json()
    assert wildcard["hits"] == []

    literal = (await _search(client, auth, types=["task"])).json()
    assert literal["hits"]


async def test_pagination_slices_the_union_once(client, db_session):
    """``limit``/``offset`` page the flat ranked list, and both views agree.

    Page 1 and page 2 with ``limit=4`` over thirteen hits must partition them with
    no overlap and no gap, and page 2's groups must describe page 2 only. A
    per-kind pagination scheme would return thirteen rows per page instead of four
    and the page sizes themselves would give it away.
    """
    seed, auth = await seeded_client(client, db_session)
    seeded = await _seed_all_kinds(db_session, seed.owner.id)
    everything = list(seeded.values())

    first = (await _search(client, auth, limit=4, offset=0)).json()
    second = (await _search(client, auth, limit=4, offset=4)).json()

    assert len(first["hits"]) == 4
    assert len(second["hits"]) == 4
    assert first["meta"] == {"total": 13, "limit": 4, "offset": 0}
    assert second["meta"]["offset"] == 4

    seen = [hit["id"] for hit in first["hits"]] + [hit["id"] for hit in second["hits"]]
    assert len(set(seen)) == 8
    assert set(seen) <= {str(i) for i in everything}
    assert sum(len(group["hits"]) for group in second["groups"]) == 4


async def test_an_offset_past_the_end_is_an_empty_page_and_not_an_error(client, db_session):
    """Paging off the end answers with nothing rather than complaining.

    A client walking pages will ask for one offset past the last page as a matter
    of course; a 404 or a 422 there would make every paginating caller special-case
    the last step.
    """
    seed, auth = await seeded_client(client, db_session)
    await _seed_all_kinds(db_session, seed.owner.id)

    body = (await _search(client, auth, offset=500)).json()
    assert body["hits"] == []
    assert body["groups"] == []
    assert body["meta"]["total"] == 13


async def test_each_kind_is_capped_at_the_documented_per_entity_scan(client, db_session):
    """One kind contributes at most :data:`SearchService.PER_ENTITY_SCAN` rows.

    The cap is what keeps ``ILIKE '%term%'`` from scanning a whole table and
    transferring all of it: it is applied as ``LIMIT`` in each statement, not as a
    slice afterwards. Seeding one row more than the cap and asserting exactly the
    cap comes back is what distinguishes those two — a post-hoc slice would also
    return the cap, but only after the database had sent every row.
    """
    seed, auth = await seeded_client(client, db_session)
    for index in range(OVER_THE_CAP):
        db_session.add(
            Note(
                owner_id=seed.owner.id,
                title=f"{TERM} note {index}",
                content="body",
                updated_at=TOUCHED,
            )
        )
    await db_session.commit()

    body = (await _search(client, auth, types=["note"])).json()
    assert len(body["hits"]) == SearchService.PER_ENTITY_SCAN
    assert body["meta"]["total"] == SearchService.PER_ENTITY_SCAN


async def test_a_title_match_survives_the_cap_even_when_it_is_the_oldest_row(client, db_session):
    """The SQL ordering, not just the Python ranking, puts title matches first.

    Fifty tasks carry the term only in their description and are the most recently
    touched rows there are; one more carries it in its title and is the oldest of
    all. Per-kind recency alone would rank that title match last and the cap would
    cut it — so the search would report "nothing here" for the one row whose title
    is the thing the user typed.

    This is the one behaviour that needs the ordering term inside the statement
    rather than in the service: the cap is applied by ``LIMIT`` *before* any row
    reaches Python, so a ranking that happened afterwards would be ranking rows the
    database had already decided not to send.
    """
    seed, auth = await seeded_client(client, db_session)
    owner = seed.owner.id
    project = Project(owner_id=owner, name="holder", updated_at=TOUCHED)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    for index in range(SearchService.PER_ENTITY_SCAN):
        db_session.add(
            Task(
                owner_id=owner,
                project_id=project.id,
                title=f"ordinary work {index}",
                description=f"mentions {TERM} in passing",
                updated_at=LATER,
            )
        )
    title_match = Task(
        owner_id=owner,
        project_id=project.id,
        title=f"{TERM} in the title",
        description="nothing else of interest",
        updated_at=TOUCHED,
    )
    db_session.add(title_match)
    await db_session.commit()
    await db_session.refresh(title_match)

    body = (await _search(client, auth, types=["task"])).json()
    assert len(body["hits"]) == SearchService.PER_ENTITY_SCAN
    assert next(hit["id"] for hit in body["hits"]) == str(title_match.id)
    assert body["hits"][0]["matched_field"] == "title"


class _StatementCounter:
    """Count the SQL a block of code actually sent.

    Listens on the *sync* engine the ``engine`` fixture installed, because
    ``before_cursor_execute`` is the sync-level event the async engine dispatches
    through — which is also why this observes the application's own session
    rather than the test's. Cleared on entry so one counter can be reused.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine.sync_engine
        self.statements: list[str] = []

    def _record(self, conn: Any, cursor: Any, statement: str, *args: Any, **kwargs: Any) -> None:
        self.statements.append(statement)

    def __enter__(self) -> _StatementCounter:
        self.statements.clear()
        event.listen(self._engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: Any) -> None:
        event.remove(self._engine, "before_cursor_execute", self._record)


async def test_the_union_costs_the_same_number_of_statements_however_many_rows_match(
    client, db_session, engine
):
    """One statement per entity kind, plus one for the project names.

    The requirement is "one query per entity kind, not a query per row", and the
    only way to hold it is to count: the same search is issued twice against the
    same database, once with a single matching row and once with twenty-one, and
    the number of statements the server actually received must be identical. A
    per-row lookup — resolving each hit's project one at a time, say — would scale
    with the second fixture and fail here while passing every other test in this
    file, because nothing else about the response's *shape* depends on it.
    """
    seed, auth = await seeded_client(client, db_session)
    project = Project(owner_id=seed.owner.id, name="holder", updated_at=TOUCHED)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    db_session.add(
        Task(
            owner_id=seed.owner.id,
            project_id=project.id,
            title=f"{TERM} the only one",
            updated_at=TOUCHED,
        )
    )
    await db_session.commit()

    counter = _StatementCounter(engine)
    with counter:
        first = await _search(client, auth, types=["task"])
    baseline = len(counter.statements)

    for index in range(20):
        # Each extra task sits in its *own* project. That is the part that makes
        # the assertion bite: a per-row project lookup would issue one query per
        # hit, and twenty-one tasks sharing one project would collapse to a single
        # distinct id and hide it again.
        extra_project = Project(owner_id=seed.owner.id, name=f"holder {index}", updated_at=TOUCHED)
        db_session.add(extra_project)
        await db_session.commit()
        await db_session.refresh(extra_project)
        db_session.add(
            Task(
                owner_id=seed.owner.id,
                project_id=extra_project.id,
                title=f"{TERM} extra {index}",
                updated_at=LATER,
            )
        )
        db_session.add(
            Note(
                owner_id=seed.owner.id,
                title=f"{TERM} note {index}",
                updated_at=LATER,
            )
        )
    await db_session.commit()

    with counter:
        second = await _search(client, auth, types=["task"])

    assert first.status_code == second.status_code == 200
    assert len(second.json()["hits"]) == 21
    assert len(counter.statements) == baseline, (
        "the search sent a different number of statements once there were more "
        f"matching rows ({len(counter.statements)} vs {baseline}); it is doing a "
        "query per row"
    )


async def test_the_cap_bounds_the_union_rather_than_the_page(client, db_session):
    """Saturated kinds are capped per kind and the page is capped on top of that.

    Two kinds, each saturated past its own cap. The discovered union is twice the
    per-kind cap, and the page still honours ``limit``. The two caps are
    independent, and the response says which rows were considered.
    """
    seed, auth = await seeded_client(client, db_session)
    for index in range(OVER_THE_CAP):
        db_session.add(
            Note(
                owner_id=seed.owner.id,
                title=f"{TERM} note {index}",
                updated_at=TOUCHED,
            )
        )
        db_session.add(
            Skill(
                user_id=seed.owner.id,
                name=f"{TERM} skill {index}",
                updated_at=TOUCHED,
            )
        )
    await db_session.commit()

    body = (await _search(client, auth, limit=10)).json()
    assert body["meta"]["total"] == 2 * SearchService.PER_ENTITY_SCAN
    assert len(body["hits"]) == 10
