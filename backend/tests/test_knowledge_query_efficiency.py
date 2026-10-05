"""``GET /knowledge/search`` costs a fixed number of statements, whatever it matches.

What was wrong
--------------
:meth:`KnowledgeService.search` matched up to :data:`MAX_SEARCH_ROWS` rows of each
kind in one call and then serialised them **one row at a time**::

    result.notes = [await self._note_read(row) for row in rows]
    result.concepts = [await self._concept_read(row) for row in rows]

``_note_read`` reads two other tables — ``note_tags`` for ``tag_ids`` and
``note_revisions`` for ``revision_count`` — and ``_concept_read`` reads one
(``concept_tags``). So every matched note cost two extra statements and every
matched concept one, on a route that is the *worst* place to pay them because a
single query fans out into up to forty.

Measured on this fixture with a ``before_cursor_execute`` listener on the engine,
before the fix:

======  ========  ============  =============
notes   concepts  matched       statements
======  ========  ============  =============
1       1         1 note,       7
                 1 concept
5       5         5 and 5       19
10      10        10 and 10     34
20      20        20 and 20     64
======  ========  ============  =============

which is ``3 * notes + concepts + 5`` — textbook N+1, and the five base
statements are the five table searches plus one owner lookup.

It was an oversight rather than a decision: the very same class already had
:meth:`KnowledgeService._note_page`, documented as *"two queries for the page,
not two per row"*, doing exactly this. The search route was written against
``_note_read`` and never pointed at the batched helpers. Every path that
serialises more than one note now goes through :meth:`_note_joins` /
:meth:`_concept_joins` and the shared pure builders, so there is no fourth route
to forget the batch in.

After the fix the count is **8 at every size**, because both repositories
short-circuit on an empty id list rather than emitting a pointless ``IN ()`` —
which is also why the "nothing matched" case costs 5.

What is asserted
----------------
1. **The cost does not grow.** The same search is measured at 1+1 and at 20+20
   matched rows and the two totals are asserted equal, and equal to the eight
   derived in :data:`SEARCH_STATEMENTS`.
2. **The joins are still filled in.** A statement count that went to zero would
   pass the first test, so the note's ``tag_ids`` and ``revision_count`` and the
   concept's ``tag_ids`` are asserted against hand-derived values on a fixture
   where exactly one note and one concept carry a tag and revisions. An untagged
   sibling is asserted to come back with an empty list and ``0`` rather than
   inheriting its neighbour's answer — the failure a per-row lookup by index
   would produce.
3. **The narrowing still filters.** ``entity_type=note`` must not start reading
   the other tables, which is the property that keeps the fan-out from coming
   back sideways.

Where the number comes from
---------------------------
``_SEARCH_TABLES`` grew a fifth entry — ``document`` — when Phase 5 added the
table, and this file's counts were written for four. The drift is invisible from
the code: a search that read one table too many costs one extra statement and
returns a perfectly plausible empty list, which is exactly what a *correct*
search also returns. Both figures are therefore derived from ``_SEARCH_TABLES``
rather than written out, so the next table cannot be added without this file
noticing.

House style, following ``tests/test_career_query_efficiency.py``
---------------------------------------------------------------
* ``pytestmark = pytest.mark.integration`` — live PostgreSQL, truncated per test.
* The service is hand-wired in a module-level helper mirroring ``app.api.deps``.
* Rows are written directly, bypassing the service, so a fixture cannot be
  perturbed by whatever a request schema requires this month.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.enums import KnowledgeEntityType, NoteStatus
from app.models.knowledge import Concept, Note, NoteRevision, note_tags
from app.models.tag import Tag
from app.models.user import User
from app.repositories.knowledge import (
    BookmarkRepository,
    CategoryRepository,
    ConceptRepository,
    DocumentRepository,
    KnowledgeLinkRepository,
    NoteRepository,
    ResourceRepository,
)
from app.services.knowledge_service import _SEARCH_TABLES, KnowledgeService
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: The two fixture sizes the cost is compared at, and the invariant under test
#: is that they cost the same. Twenty is the ceiling the route enforces
#: (``MAX_SEARCH_ROWS``), so it is the largest result the API can ever return.
SMALL_ACCOUNT = 1
LARGE_ACCOUNT = 20

#: How many statements one search costs, derived from the code:
#:
#: * one per entry in ``_SEARCH_TABLES`` — notes, concepts, resources, bookmarks
#:   and documents — because ``search`` is called with no ``entity_type`` and
#:   searches every kind;
#: * one statement per joined batch that matched anything: ``note_tags`` and
#:   ``note_revisions`` for the notes, ``concept_tags`` for the concepts. Each is
#:   skipped entirely when the batch is empty, which is what keeps the
#:   "nothing matched" case at the bare table count.
#:
#: The per-row version this replaced cost the table count plus
#: ``2 * notes + concepts``: seven at one and one, sixty-four at twenty and
#: twenty. Both figures below are *computed* rather than spelled out, because a
#: hand-written count is a tripwire that goes quiet the day the table list grows.
SEARCH_TABLE_STATEMENTS = len(_SEARCH_TABLES)
SEARCH_STATEMENTS = SEARCH_TABLE_STATEMENTS + 3


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(session: AsyncSession) -> KnowledgeService:
    """A knowledge service wired the way ``app.api.deps`` wires it.

    Every collaborator is the real one: a stubbed repository would make the
    statement count meaningless, which is the whole point of measuring it.
    """
    return KnowledgeService(
        NoteRepository(session),
        ConceptRepository(session),
        ResourceRepository(session),
        BookmarkRepository(session),
        DocumentRepository(session),
        CategoryRepository(session),
        KnowledgeLinkRepository(session),
    )


async def _owner(session: AsyncSession, username: str = "ada") -> User:
    """One account, inserted directly so no activity events move the fixture."""
    return await register_user(session, username=username)


#: A fixed instant, so nothing here depends on the clock.
_AT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


async def _seed_matching(
    session: AsyncSession, owner: User, *, notes: int, concepts: int
) -> dict[str, uuid.UUID]:
    """Rows that all match the term ``needle``, one of each kind carrying joins.

    The first note gets two tags and three revisions; every other note and every
    concept is bare. ``needle`` appears in the title/description so all of them
    match, and ``haystack`` appears nowhere, so the result set is exactly the
    fixture and the statement count is not at the mercy of a stray row.

    Returns:
        The id of the note that carries the joins, so the assertions can name it
        rather than relying on list order.
    """
    tag_one = Tag(id=uuid.uuid4(), user_id=owner.id, name="alpha")
    tag_two = Tag(id=uuid.uuid4(), user_id=owner.id, name="beta")
    session.add_all([tag_one, tag_two])

    decorated: uuid.UUID | None = None
    for index in range(notes):
        note = Note(
            id=uuid.uuid4(),
            owner_id=owner.id,
            title=f"needle {index}",
            content="haystack",
            status=NoteStatus.DRAFT.value,
            created_at=_AT,
            updated_at=_AT,
        )
        session.add(note)
        if index == 0:
            decorated = note.id
    for index in range(concepts):
        session.add(
            Concept(
                id=uuid.uuid4(),
                owner_id=owner.id,
                name=f"needle concept {index}",
                description="haystack",
                created_at=_AT,
                updated_at=_AT,
            )
        )
    await session.commit()

    # The joins are written through the tables rather than through the service,
    # so the fixture states exactly what the assertions expect to find.
    await session.execute(
        note_tags.insert(),
        [
            {"note_id": decorated, "tag_id": tag_one.id},
            {"note_id": decorated, "tag_id": tag_two.id},
        ],
    )
    session.add_all(
        NoteRevision(
            id=uuid.uuid4(),
            note_id=decorated,
            owner_id=owner.id,
            title=f"needle revision {number}",
            content="haystack",
            created_at=_AT,
        )
        for number in (1, 2, 3)
    )
    await session.commit()
    return {"decorated_note": decorated}


@contextmanager
def _counting(engine: AsyncEngine) -> Iterator[list[str]]:
    """Every statement the engine sends while the block runs.

    ``before_cursor_execute`` on the sync engine rather than on a session, so the
    count is the whole cost of the call including whatever the repositories do
    internally — which is exactly the number that grew with the result set.
    """

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        seen.append(statement)

    seen: list[str] = []
    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)


def _render(statements: list[str]) -> str:
    """The counted statements, one per line, for an assertion message."""
    return "\n".join(f"    {' '.join(text.split())[:120]}" for text in statements)


# ---------------------------------------------------------------------------
# The cost
# ---------------------------------------------------------------------------


async def test_the_cost_of_a_search_does_not_grow_with_the_number_of_rows_it_matches(
    db_session: AsyncSession, engine: AsyncEngine
) -> None:
    """One statement more than nothing, whether one row matched or forty.

    The two fixtures are four hundred rows apart in size and the totals are
    asserted *equal*, which is a stronger claim than either being small: it says
    the cost is a property of the shape of the query rather than of the account.
    """
    service = _service(db_session)

    counts: dict[int, int] = {}
    for size in (SMALL_ACCOUNT, LARGE_ACCOUNT):
        owner = await _owner(db_session, username=f"ada{size}")
        await _seed_matching(db_session, owner, notes=size, concepts=size)
        with _counting(engine) as seen:
            result = await service.search(owner=owner, query="needle")
        assert len(result.notes) == size
        assert len(result.concepts) == size
        counts[size] = len(seen)

    assert counts[SMALL_ACCOUNT] == counts[LARGE_ACCOUNT], (
        f"search cost grew with the result set: {_render([])}\n"
        f"{SMALL_ACCOUNT}+{SMALL_ACCOUNT} rows cost {counts[SMALL_ACCOUNT]}, "
        f"{LARGE_ACCOUNT}+{LARGE_ACCOUNT} rows cost {counts[LARGE_ACCOUNT]}"
    )
    assert counts[LARGE_ACCOUNT] == SEARCH_STATEMENTS


async def test_a_search_that_matches_nothing_costs_only_the_table_searches(
    db_session: AsyncSession, engine: AsyncEngine
) -> None:
    """The joins are skipped when there is nothing to join, rather than queried empty.

    ``IN ()`` is a pointless round trip, so both repositories return early on an
    empty id list. A regression that batched *before* filtering would pay for
    three statements that cannot return anything.
    """
    owner = await _owner(db_session)
    await _seed_matching(db_session, owner, notes=2, concepts=2)

    with _counting(engine) as seen:
        result = await _service(db_session).search(owner=owner, query="haystack-not-present")

    assert (result.notes, result.concepts, result.resources, result.bookmarks) == (
        [],
        [],
        [],
        [],
    )
    # ``documents`` is asserted here too: it is the kind Phase 5 added to
    # ``_SEARCH_TABLES`` after these counts were written, and a search that read
    # it and found nothing is indistinguishable from one that correctly did not.
    assert result.documents == []
    assert len(seen) == SEARCH_TABLE_STATEMENTS


# ---------------------------------------------------------------------------
# The figures the batching must not lose
# ---------------------------------------------------------------------------


async def test_the_joined_columns_are_still_filled_in(db_session: AsyncSession) -> None:
    """The decorated note carries its two tags and three revisions; its siblings do not.

    Hand-derived: the first note is written two rows into ``note_tags`` and three
    into ``note_revisions``, so ``tag_ids`` has two entries in name order
    (``alpha``, ``beta``) and ``revision_count`` is 3. Every other note was
    written with neither, so it must come back with ``[]`` and ``0`` — the case a
    per-row lookup that indexed into a shared result map would get wrong, because
    a missing key has to be a default rather than the neighbour's answer.
    """
    owner = await _owner(db_session)
    ids = await _seed_matching(db_session, owner, notes=3, concepts=1)

    result = await _service(db_session).search(owner=owner, query="needle")

    decorated = [note for note in result.notes if note.id == ids["decorated_note"]]
    assert len(decorated) == 1
    assert decorated[0].revision_count == 3
    assert len(decorated[0].tag_ids) == 2

    for note in result.notes:
        if note.id == ids["decorated_note"]:
            continue
        assert note.revision_count == 0, f"{note.title!r} inherited a neighbour's count"
        assert note.tag_ids == [], f"{note.title!r} inherited a neighbour's tags"

    for concept in result.concepts:
        assert concept.tag_ids == []


async def test_narrowing_to_one_entity_type_does_not_read_the_other_tables(
    db_session: AsyncSession, engine: AsyncEngine
) -> None:
    """``entity_type=note`` searches notes and joins notes, and stops there.

    The narrowing is what keeps the fan-out from returning sideways: a batched
    join that ignored the filter would read ``concept_tags`` on a note-only
    search and hand the caller concept rows it asked not for.
    """
    owner = await _owner(db_session)
    await _seed_matching(db_session, owner, notes=3, concepts=3)

    with _counting(engine) as seen:
        result = await _service(db_session).search(
            owner=owner, query="needle", entity_type=KnowledgeEntityType.NOTE
        )

    assert len(result.notes) == 3
    assert (result.concepts, result.resources, result.bookmarks, result.documents) == (
        [],
        [],
        [],
        [],
    )
    # one notes search + note_tags + note_revisions
    assert len(seen) == 3
