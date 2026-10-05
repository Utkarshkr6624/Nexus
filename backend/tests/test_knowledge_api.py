"""Phase 5 knowledge base, end to end: history, edges, search and ownership.

:meth:`app.services.knowledge_service.KnowledgeService` decides everything the
knowledge surface answers — what a revision is, what a restore reverts, whether
an edge may be written, whose notes a search may return — and until this file it
had **no test at all**. The router is a translation layer over it, so these tests
go through HTTP anyway: an unexecuted service behind an unexercised router is
two untested layers, and the 404-not-403 rule the whole surface rests on is a
statement about the response a caller receives.

What the file is for
--------------------
Seven questions the audit asked of a module nothing had ever run:

**Does a revision get created before every meaningful edit?** Yes, and it has to
stay yes — including for a note whose body is still empty, which is the case a
guard in :meth:`KnowledgeService._snapshot` used to skip. See
:func:`test_renaming_a_note_that_has_no_body_yet_still_keeps_the_title_it_had`.

**Does a restore put back everything the note was?** Text yes; status no, and
that asymmetry is the point rather than an omission — a revision is a copy of
the *text*, and reverting the lifecycle with it would un-publish a note on the
strength of an edit made after publishing. Tags are not part of a revision at
all; see "found and not fixed" below.

**Is the backlink index consistent?** Every edge touching a deleted note goes
with it, in both directions, because ``knowledge_links`` carries no foreign key
for a cascade to hang off. :func:`test_deleting_a_note_removes_the_edges_that_arrive_at_it_as_well_as_the_ones_that_leave_it`
pins the absence of a surviving backlink.

**Can a note link to itself, and can two concepts cycle?** The self edge is
refused in the service and in a ``CHECK`` constraint; every other cycle is legal
and meaningful — a note that explains a concept which references it back is two
real statements, not a bug — and both facts are pinned here so that a later
"defensive" cycle check would have to be argued for rather than slipped in.

**Does deleting a note clean up after it?** Revisions and tag rows cascade;
edges do not and are removed explicitly.

**Is the search a parameterised query, and can a filter widen the scope?** Both,
and cross-account search is asserted directly rather than inferred from the
scoping argument. Bookmarks were missing from it entirely — see
:func:`test_search_covers_bookmarks_as_well_as_the_other_three_kinds`.

**Does note content ever reach a raw-HTML sink?** Not from this API: content is
stored and returned byte-for-byte as text, and no endpoint renders HTML. The
rendering happens in the client, in ``frontend/src/types/knowledge.ts``
(:func:`renderMarkdown`), which escapes user text before emitting any tag and
refuses a non-``http(s)`` href; :func:`test_note_content_containing_html_and_a_javascript_link_is_stored_and_returned_as_text`
pins the half this repository owns, and :func:`test_a_javascript_url_is_refused_for_a_bookmark`
pins the server-side half of the same rule.

Four defects these tests found, and their fixes
-----------------------------------------------
Recorded here because the tests below are what hold the corrected behaviour, and
because each failure has a shape a later refactor could reintroduce silently.

1. **A rename of a note with no body was never snapshotted.**
   :meth:`KnowledgeService._snapshot` skipped the write when ``content`` and
   ``summary`` were both empty, reasoning that a revision of an empty note
   describes nothing. But ``title`` is ``NOT NULL`` and is one of the three
   columns a revision stores, so the note that guard spared was a *titled* note
   whose body had not been written yet — and the edit it skipped was a rename.
   :meth:`KnowledgeService.update_note` had already classified that edit as
   meaningful, so the title the user typed before the first keystroke was
   destroyed with nothing to restore it from. The guard is gone: the copy taken
   is the creation state, which is exactly what a first edit wants back, and the
   history is bounded either way.

2. **Search never looked at bookmarks.** ``wanted`` was built from the *graph's*
   list of node types, because a bookmark is not a graph node and the two lists
   had been shared. The ``if "bookmark" in wanted`` branch was therefore
   unreachable, ``result.bookmarks`` was permanently empty, and a caller could
   not tell "no bookmark matched" from "no bookmark was ever looked for" — a
   measured zero indistinguishable from an absence of measurement. The search now
   has its own list including bookmarks; the graph keeps its own.

3. **A PATCH sending ``null`` for a ``NOT NULL`` column was a 500.** Every
   ``*Update`` model types its required fields ``str | None`` so that *omitting*
   one stays legal, which also makes ``{"title": null}`` valid — and it then
   reached a ``NOT NULL`` column. For a note and a document that is an
   unhandled ``NotNullViolation``; for a concept, a category or a bookmark the
   ``IntegrityError`` handler turned it into a **409 claiming a name the caller
   already had**, which is a confident falsehood about somebody else's data.
   :func:`KnowledgeService._refuse_null` answers those requests with the 422 they
   were written for, before anything is written — and, on the note path, before
   the snapshot, so a refused edit leaves no history behind.

4. **``revision_count`` was 0 on every single-note response.** It is not a
   column, and Pydantic fills a response field from its default when the object
   it is validating does not carry it. The listing and the search build
   :class:`NoteRead` explicitly and were right; the read, the PATCH, ``/publish``,
   ``/archive``, ``/restore`` and ``/restore-revision`` returned the bare row and
   reported a note with fifty revisions as having none. Zero is the answer for a
   note that has never been edited, so this is precisely the confusion the rule
   about absence versus a measured zero exists to prevent — and it was the field
   Phase 10 would have trained on.
   :meth:`KnowledgeService._annotate` now attaches both derived values to the row
   before it is serialised.

Found and not fixed
-------------------
* **A revision carries no tags, so a restore cannot revert them.** Correct as it
  stands: no code path applies ``tag_ids`` to a note (see the module docstring of
  ``app.services.knowledge_service``), so a note's ``tag_ids`` are always empty
  and there is no tag describing stale content. The fix is a
  ``tag_ids`` column on ``note_revisions`` plus the scoped tag lookup the module
  says is missing — both in files this wave does not own.
* **The ``CODE_SENTINEL`` in ``frontend/src/types/knowledge.ts:633`` is described
  as a character that "cannot occur in the source"**. A private-use code point
  can be pasted into a note, and a note containing two of them with digits between
  is silently edited by the restore regex at ``knowledge.ts:665``. It deletes
  characters from prose or substitutes a code span; it cannot inject markup,
  because every substitute is escaped. Frontend file, not owned here.

House style
-----------
Follows ``tests/test_developer_service.py``:

* ``pytestmark = pytest.mark.integration`` — every test here needs the live
  PostgreSQL the suite truncates between tests.
* Rows are read back through **explicit column tuples**, never ORM entities: the
  API writes through a different session than the one the assertions read, but a
  ``select(Tag)`` would still hand back the identity-mapped object, and a
  cascade assertion that compares a cached object to a deleted row can pass for
  the wrong reason.
* Expected figures are derived in each docstring rather than recorded from a run.
* Test names are full English sentences.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivityLog
from app.models.knowledge import (
    MAX_REVISIONS_PER_NOTE,
    KnowledgeLink,
    NoteRevision,
    note_tags,
)
from app.models.tag import Tag
from app.schemas.knowledge import KnowledgeSearchKind
from tests.analytics_fixtures import seeded_client

pytestmark = pytest.mark.integration

#: The knowledge router's mount point.
KNOWLEDGE = "/api/v1/knowledge"

#: One more edit than the cap, so the prune has something to remove. Derived, not
#: chosen for a round number: ``MAX_REVISIONS_PER_NOTE + 3`` puts the oldest
#: survivor three edits from the start, which is what the assertion below reads.
EDITS_OVER_THE_CAP = MAX_REVISIONS_PER_NOTE + 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _new_note(client, auth: dict[str, str], **fields: Any) -> dict[str, Any]:
    """Create a note through the API and return its body.

    ``title`` defaults so a test that cares about the body says only the body.
    """
    payload = {"title": "A note", **fields}
    response = await client.post(f"{KNOWLEDGE}/notes", json=payload, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


async def _patch_note(client, auth: dict[str, str], note_id: uuid.UUID, **fields: Any) -> Any:
    """PATCH a note and return the raw response, status code included."""
    return await client.patch(f"{KNOWLEDGE}/notes/{note_id}", json=fields, headers=auth)


async def _revisions(
    db_session: AsyncSession, note_id: uuid.UUID
) -> list[tuple[str, str, str | None]]:
    """One note's revisions as ``(title, content, summary)``, **oldest first**.

    Explicit columns rather than ``NoteRevision`` entities: the assertions below
    compare a revision against the state it was taken from, and an identity-mapped
    row written through the API's own session would make that comparison moot.
    """
    result = await db_session.execute(
        select(NoteRevision.title, NoteRevision.content, NoteRevision.summary)
        .where(NoteRevision.note_id == note_id)
        .order_by(NoteRevision.created_at.asc(), NoteRevision.id.asc())
    )
    return list(result.all())


async def _revision_count(db_session: AsyncSession, note_id: uuid.UUID) -> int:
    """How many revisions of one note survive."""
    return int(
        await db_session.scalar(
            select(func.count()).select_from(NoteRevision).where(NoteRevision.note_id == note_id)
        )
    )


async def _edges(db_session: AsyncSession) -> list[tuple[str, uuid.UUID, str, uuid.UUID]]:
    """Every edge in the table as ``(source_type, source_id, target_type, target_id)``.

    The whole table, deliberately: the question every delete test asks is whether
    *any* row is left naming a row that no longer exists, and scoping the query to
    the deleted id would answer a weaker one.
    """
    result = await db_session.execute(
        select(
            KnowledgeLink.source_type,
            KnowledgeLink.source_id,
            KnowledgeLink.target_type,
            KnowledgeLink.target_id,
        )
    )
    return list(result.all())


async def _link_count(db_session: AsyncSession) -> int:
    """How many edges exist at all."""
    return int(await db_session.scalar(select(func.count()).select_from(KnowledgeLink)))


async def _tag_row_count(db_session: AsyncSession, note_id: uuid.UUID) -> int:
    """How many ``note_tags`` rows point at one note."""
    return int(
        await db_session.scalar(
            select(func.count()).select_from(note_tags).where(note_tags.c.note_id == note_id)
        )
    )


async def _seed_tag_row(
    db_session: AsyncSession, *, user_id: uuid.UUID, note_id: uuid.UUID
) -> None:
    """Give a note a tag the only way the current code allows: directly.

    No endpoint applies ``tag_ids`` to a note — ``NoteRepository.set_tags`` has no
    caller — so the cascade claim can only be tested by writing the association
    row by hand. That is the point of using it here and not elsewhere: the test is
    about what a delete does to a row the database will have to clean up.
    """
    tag = Tag(user_id=user_id, name="cascade-probe")
    db_session.add(tag)
    await db_session.commit()
    await db_session.execute(insert(note_tags).values(note_id=note_id, tag_id=tag.id))
    await db_session.commit()


async def _link(
    client,
    auth: dict[str, str],
    *,
    source_type: str,
    source_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    link_type: str = "related_to",
) -> Any:
    """Record one edge and return the raw response."""
    return await client.post(
        f"{KNOWLEDGE}/links",
        json={
            "source_type": source_type,
            "source_id": str(source_id),
            "target_type": target_type,
            "target_id": str(target_id),
            "link_type": link_type,
        },
        headers=auth,
    )


# ---------------------------------------------------------------------------
# Revisions: is a copy written before every meaningful edit?
# ---------------------------------------------------------------------------


async def test_every_meaningful_edit_writes_a_revision_of_the_state_it_displaced(
    client, db_session
):
    """A note's history holds the state *before* each edit, not the state after.

    Two edits, so the order is visible rather than assumed: the note is created
    with body ``first``, edited to ``second`` and then to ``third``, and the
    history must then hold exactly two entries reading ``first`` and ``second`` in
    that order. A revision written *after* the update would hold ``second`` and
    ``third`` instead, and the newest edit would be the only one with nothing to
    go back to.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="first")

    assert (await _patch_note(client, auth, note["id"], content="second")).status_code == 200
    assert (await _patch_note(client, auth, note["id"], content="third")).status_code == 200

    assert await _revisions(db_session, note["id"]) == [
        ("A note", "first", None),
        ("A note", "second", None),
    ]


async def test_renaming_a_note_that_has_no_body_yet_still_keeps_the_title_it_had(
    client, db_session
):
    """A note created with no body and then renamed keeps the title it had.

    Regression. ``title`` is ``NOT NULL`` and is one of the three columns a
    revision stores, so a note with an empty body is not an empty note — it is a
    titled note that has not been written yet. The guard this replaces skipped the
    snapshot whenever ``content`` and ``summary`` were both empty, which is
    exactly the state a rename is made from; the old title was destroyed by the
    rename and nothing could restore it, even though the service had classified
    the edit as meaningful. One rename therefore produces exactly one revision,
    holding the pre-rename title, and restoring it puts the title back.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth)

    assert note["content"] == ""
    response = await _patch_note(client, auth, note["id"], title="Renamed title")
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed title"

    history = await _revisions(db_session, note["id"])
    assert len(history) == 1
    assert history[0] == ("A note", "", None)

    revision_id = await _revision_id(db_session, note["id"])
    restored = await client.post(
        f"{KNOWLEDGE}/notes/{note['id']}/restore-revision/{revision_id}",
        headers=auth,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["title"] == "A note"


async def _revision_id(db_session: AsyncSession, note_id: uuid.UUID) -> uuid.UUID:
    """The newest revision id of one note, as the history listing would order it."""
    revision_id = await db_session.scalar(
        select(NoteRevision.id)
        .where(NoteRevision.note_id == note_id)
        .order_by(NoteRevision.created_at.desc(), NoteRevision.id.desc())
        .limit(1)
    )
    assert revision_id is not None
    return revision_id


async def test_a_patch_that_changes_nothing_writes_no_revision(client, db_session):
    """Sending the same title back is not an edit.

    The test for "meaningful" is against the stored row, not against which keys
    the payload named: a PATCH that sets the title to the title it already has
    leaves the note identical, and a history entry distinguishing nothing is
    worse than no entry because it makes the count a client shows untrue.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="body")

    response = await _patch_note(client, auth, note["id"], title="A note", content="body")
    assert response.status_code == 200, response.text
    assert response.json()["revision_count"] == 0
    assert await _revisions(db_session, note["id"]) == []


async def test_publishing_a_note_writes_no_revision(client, db_session):
    """A lifecycle transition changes no text and is not an edit.

    The history is a record of what the note *said*; a copy taken on publish would
    be indistinguishable from the copy taken by the next keystroke, and would push
    a genuinely older state out of the bounded window.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="body")

    response = await client.post(f"{KNOWLEDGE}/notes/{note['id']}/publish", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"
    assert response.json()["revision_count"] == 0
    assert await _revisions(db_session, note["id"]) == []


async def test_history_is_capped_and_keeps_the_newest_revisions(client, db_session):
    """More than ``MAX_REVISIONS_PER_NOTE`` edits leave exactly the cap, newest kept.

    The cap is :data:`~app.models.knowledge.MAX_REVISIONS_PER_NOTE` = 50 and the
    note is edited ``50 + 3`` = 53 times, starting from body ``0``. Every edit
    displaces one body, so 53 edits produce bodies ``0``-``52``: 53 revisions,
    of which the newest 50 survive. Sorted oldest first that is ``3``-``52``, so
    the first survivor is ``body-3`` and the last is ``body-52`` — a prefix is the
    wrong answer here, because pruning the oldest end of a *creation-ordered*
    window would drop the state the newest revision is about to be compared with.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="body-0")

    for edit in range(1, EDITS_OVER_THE_CAP + 1):
        response = await _patch_note(client, auth, note["id"], content=f"body-{edit}")
        assert response.status_code == 200, response.text

    history = await _revisions(db_session, note["id"])
    assert len(history) == MAX_REVISIONS_PER_NOTE
    bodies = [content for _title, content, _summary in history]
    assert bodies[0] == "body-3"
    assert bodies[-1] == f"body-{EDITS_OVER_THE_CAP - 1}"
    assert bodies == [f"body-{index}" for index in range(3, EDITS_OVER_THE_CAP)]

    read = await client.get(f"{KNOWLEDGE}/notes/{note['id']}", headers=auth)
    assert read.json()["revision_count"] == MAX_REVISIONS_PER_NOTE


async def test_a_single_note_read_reports_the_revision_count_its_history_actually_holds(
    client, db_session
):
    """``revision_count`` is the history's real size on every note route, not zero.

    Regression. ``revision_count`` is not a column on ``notes``, and Pydantic fills
    a response field from its default when the object being validated does not
    carry it. The listing and the search built :class:`NoteRead` explicitly and
    were right; every route that hands a note straight back — the read, the PATCH,
    ``/publish``, ``/restore-revision`` — returned the bare row and answered
    ``0``. Two edits followed by a read, a PATCH and a lifecycle call must each
    report 2, because 0 is the answer for a note that has *never* been edited and
    is a lie about one that has. The count is asserted on the row itself too, so
    the figure is compared with what the table holds rather than with a constant.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="first")

    fresh = await client.get(f"{KNOWLEDGE}/notes/{note['id']}", headers=auth)
    assert fresh.json()["revision_count"] == 0

    await _patch_note(client, auth, note["id"], content="second")
    await _patch_note(client, auth, note["id"], content="third")
    held = await _revision_count(db_session, note["id"])
    assert held == 2

    read = await client.get(f"{KNOWLEDGE}/notes/{note['id']}", headers=auth)
    assert read.json()["revision_count"] == held

    patched = await _patch_note(client, auth, note["id"], summary="a summary")
    assert patched.json()["revision_count"] == held + 1

    published = await client.post(f"{KNOWLEDGE}/notes/{note['id']}/publish", headers=auth)
    assert published.json()["revision_count"] == held + 1

    listing = await client.get(f"{KNOWLEDGE}/notes", headers=auth)
    assert listing.json()["items"][0]["revision_count"] == held + 1


# ---------------------------------------------------------------------------
# Revisions: what does a restore put back?
# ---------------------------------------------------------------------------


async def test_restoring_a_revision_puts_the_text_back_and_leaves_the_status_alone(
    client, db_session
):
    """A restore reverts the text and only the text.

    The note is edited, then published, then rolled back to the revision taken
    before the edit. The body must go back while the status stays ``published``:
    a revision stores the note's *text*, and reverting the lifecycle with it
    would un-publish the note on the strength of an edit made after publishing —
    a claim the user never made. Status moves only through ``/publish``,
    ``/archive`` and ``/restore``.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="original")
    await _patch_note(client, auth, note["id"], content="edited")
    revision_id = await _revision_id(db_session, note["id"])

    published = await client.post(f"{KNOWLEDGE}/notes/{note['id']}/publish", headers=auth)
    assert published.json()["status"] == "published"

    restored = await client.post(
        f"{KNOWLEDGE}/notes/{note['id']}/restore-revision/{revision_id}", headers=auth
    )
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["content"] == "original"
    assert body["title"] == note["title"]
    assert body["status"] == "published"


async def test_a_restore_is_itself_undoable_because_the_state_it_displaced_is_kept(
    client, db_session
):
    """Restoring appends; it does not consume.

    Two revisions after one restore — the one taken before the edit and the one
    taken by the restore itself — and restoring the newer of the two returns the
    note to the text that had been displaced. Consuming the revision instead would
    make "I changed my mind" permanent, which is the one thing a history feature
    exists to prevent.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="original")
    await _patch_note(client, auth, note["id"], content="edited")
    first_revision = await _revision_id(db_session, note["id"])

    response = await client.post(
        f"{KNOWLEDGE}/notes/{note['id']}/restore-revision/{first_revision}", headers=auth
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == "original"

    assert await _revision_count(db_session, note["id"]) == 2
    undo_revision = await _revision_id(db_session, note["id"])
    undone = await client.post(
        f"{KNOWLEDGE}/notes/{note['id']}/restore-revision/{undo_revision}",
        headers=auth,
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["content"] == "edited"


async def test_a_revision_id_from_another_note_cannot_be_restored_onto_this_one(client, db_session):
    """The revision lookup carries ``note_id``, ``revision_id`` and ``owner_id``.

    The caller owns both notes in this test, so nothing here is a cross-tenant
    question: it is the pairing of ids. Looking the revision up by id alone and
    checking the note afterwards would confirm a real revision against a note it
    does not belong to, and the history panel would offer a rollback that writes
    the wrong prose onto this note.
    """
    _, auth = await seeded_client(client, db_session)
    first = await _new_note(client, auth, content="first body")
    second = await _new_note(client, auth, content="second body")
    await _patch_note(client, auth, first["id"], content="first edited")
    stray_revision = await _revision_id(db_session, first["id"])

    response = await client.post(
        f"{KNOWLEDGE}/notes/{second['id']}/restore-revision/{stray_revision}", headers=auth
    )
    assert response.status_code == 404, response.text

    unchanged = await client.get(f"{KNOWLEDGE}/notes/{second['id']}", headers=auth)
    assert unchanged.json()["content"] == "second body"


# ---------------------------------------------------------------------------
# Edges: is the backlink index consistent?
# ---------------------------------------------------------------------------


async def test_deleting_a_note_removes_the_edges_that_arrive_at_it_and_leave_it(client, db_session):
    """No edge survives its endpoint, in either direction.

    ``knowledge_links`` carries no foreign key on either endpoint — ``source_type``
    is what decides which table the id names, so no FK can be written — and
    therefore nothing cascades. Three edges are created around one note: one it
    points at, one that points at it, and one that both leaves and arrives at it.
    After the delete the table must be **empty**, not merely free of that note's
    *outgoing* edges: a surviving row naming the deleted id is a backlink that
    answers with a node no screen can render, and the graph would keep drawing
    into it.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, title="The note")
    other = await _new_note(client, auth, title="The other note")
    concept = await client.post(f"{KNOWLEDGE}/concepts", json={"name": "backlinks"}, headers=auth)
    concept_id = concept.json()["id"]

    outbound = await _link(
        client,
        auth,
        source_type="note",
        source_id=note["id"],
        target_type="concept",
        target_id=concept_id,
    )
    inbound = await _link(
        client,
        auth,
        source_type="note",
        source_id=other["id"],
        target_type="note",
        target_id=note["id"],
    )
    both_ways = await _link(
        client,
        auth,
        source_type="note",
        source_id=note["id"],
        target_type="note",
        target_id=other["id"],
    )
    assert {outbound.status_code, inbound.status_code, both_ways.status_code} == {201}
    assert await _link_count(db_session) == 3

    deleted = await client.delete(f"{KNOWLEDGE}/notes/{note['id']}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    assert await _edges(db_session) == []
    graph = await client.get(f"{KNOWLEDGE}/graph", headers=auth)
    assert graph.json()["edges"] == []


async def test_deleting_one_edge_leaves_both_of_its_endpoints_alone(client, db_session):
    """Removing an edge is a statement that the relationship was wrong.

    Neither note goes with it, and the remaining edge is still listed. The
    interesting half is the backlink: a note that had two edges pointing *into*
    it must show one after one is deleted, so the listing is read as a sequence
    rather than as "non-empty".
    """
    _, auth = await seeded_client(client, db_session)
    target = await _new_note(client, auth, title="Target")
    first = await _new_note(client, auth, title="First source")
    second = await _new_note(client, auth, title="Second source")

    kept = await _link(
        client,
        auth,
        source_type="note",
        source_id=first["id"],
        target_type="note",
        target_id=target["id"],
    )
    removed = await _link(
        client,
        auth,
        source_type="note",
        source_id=second["id"],
        target_type="note",
        target_id=target["id"],
    )
    assert kept.status_code == removed.status_code == 201

    deleted = await client.delete(f"{KNOWLEDGE}/links/{removed.json()['id']}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    backlinks = await client.get(
        f"{KNOWLEDGE}/links",
        params={"target_type": "note", "target_id": str(target["id"])},
        headers=auth,
    )
    assert backlinks.json()["meta"]["total"] == 1
    assert [item["source_id"] for item in backlinks.json()["items"]] == [str(first["id"])]

    for survivor in (target, first, second):
        assert (
            await client.get(f"{KNOWLEDGE}/notes/{survivor['id']}", headers=auth)
        ).status_code == 200


async def test_an_edge_naming_another_accounts_note_is_refused_and_writes_nothing(
    client, db_session
):
    """A link endpoint is resolved through an owner-scoped query *before* the write.

    Grace's note as the target of Ada's edge is a 404, and the table is still
    empty afterwards. A guard applied after the insert would be a guard applied to
    a row that already exists, so the resolution has to happen first and the
    refusal has to leave no edge, no activity event and nothing to roll back.
    """
    _, ada_auth = await seeded_client(client, db_session)
    _, grace_auth = await seeded_client(client, db_session, username="grace")
    ada_note = await _new_note(client, ada_auth)
    grace_note = await _new_note(client, grace_auth)

    response = await _link(
        client,
        ada_auth,
        source_type="note",
        source_id=ada_note["id"],
        target_type="note",
        target_id=grace_note["id"],
    )
    assert response.status_code == 404, response.text
    assert await _link_count(db_session) == 0


async def test_asking_for_another_accounts_backlinks_is_a_404_rather_than_their_edges(
    client, db_session
):
    """``GET /knowledge/links`` takes no id in the path, so the service must check.

    Neither endpoint id is in the path and nothing in the request mentions an
    owner, which makes this the one route on the surface where nothing forces the
    scope to be applied. Grace's note has an edge into it; Ada asks for that
    node's backlinks and must be told the node does not exist rather than be
    handed the edge.
    """
    _, ada_auth = await seeded_client(client, db_session)
    _, grace_auth = await seeded_client(client, db_session, username="grace")
    grace_note = await _new_note(client, grace_auth)
    grace_other = await _new_note(client, grace_auth)
    created = await _link(
        client,
        grace_auth,
        source_type="note",
        source_id=grace_other["id"],
        target_type="note",
        target_id=grace_note["id"],
    )
    assert created.status_code == 201

    response = await client.get(
        f"{KNOWLEDGE}/links",
        params={"target_type": "note", "target_id": str(grace_note["id"])},
        headers=ada_auth,
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert str(grace_other["id"]) not in response.text


# ---------------------------------------------------------------------------
# Edges: self-edges and cycles
# ---------------------------------------------------------------------------


async def test_a_note_cannot_be_linked_to_itself(client, db_session):
    """The degenerate edge is refused, and nothing is written.

    "Note X references note X" adds nothing a reader can act on and a renderer
    asked to draw it has to special-case the loop, so it is refused twice over:
    here, and by ``ck_knowledge_links_no_self_edge`` in the database.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth)

    response = await _link(
        client,
        auth,
        source_type="note",
        source_id=note["id"],
        target_type="note",
        target_id=note["id"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert await _link_count(db_session) == 0


async def test_a_note_may_be_linked_to_another_note_of_the_same_type(client, db_session):
    """The self-edge check compares both halves, so note-to-note is still legal.

    Two notes of the same type differ only by id. A ``CHECK (source_id <>
    target_id)`` would refuse this — the commonest edge shape in a knowledge base —
    while a check on the ids alone is what makes the degenerate case detectable at
    all. Both facts are pinned here so neither half can be "simplified" later.
    """
    _, auth = await seeded_client(client, db_session)
    source = await _new_note(client, auth, title="Source")
    target = await _new_note(client, auth, title="Target")

    response = await _link(
        client,
        auth,
        source_type="note",
        source_id=source["id"],
        target_type="note",
        target_id=target["id"],
    )
    assert response.status_code == 201, response.text
    assert await _link_count(db_session) == 1


async def test_two_concepts_may_be_linked_to_each_other_in_both_directions(client, db_session):
    """A cycle is legal here, and deliberately so.

    ``A -> B`` alongside ``B -> A`` says two things — A is about B, and B is about
    A — which in a knowledge graph is content rather than damage. Only the
    *self* edge is nonsense, and only it is refused; a cycle check would refuse
    statements the model deliberately allows. The pair is written, and each
    direction is then readable from the right listing.
    """
    _, auth = await seeded_client(client, db_session)
    first = await client.post(f"{KNOWLEDGE}/concepts", json={"name": "async"}, headers=auth)
    second = await client.post(f"{KNOWLEDGE}/concepts", json={"name": "concurrency"}, headers=auth)
    assert first.status_code == second.status_code == 201

    forward = await _link(
        client,
        auth,
        source_type="concept",
        source_id=first.json()["id"],
        target_type="concept",
        target_id=second.json()["id"],
    )
    backward = await _link(
        client,
        auth,
        source_type="concept",
        source_id=second.json()["id"],
        target_type="concept",
        target_id=first.json()["id"],
    )
    assert {forward.status_code, backward.status_code} == {201}

    outbound = await client.get(
        f"{KNOWLEDGE}/links",
        params={"source_type": "concept", "source_id": str(first.json()["id"])},
        headers=auth,
    )
    assert [item["target_id"] for item in outbound.json()["items"]] == [str(second.json()["id"])]


# ---------------------------------------------------------------------------
# Deleting a note: what goes with it
# ---------------------------------------------------------------------------


async def test_deleting_a_note_removes_its_revisions_and_its_tag_rows(client, db_session):
    """A revision whose note is gone describes nothing, so it goes with it.

    Both rows carry an ``ON DELETE CASCADE`` foreign key to ``notes.id`` —
    ``note_revisions.note_id`` and ``note_tags.note_id`` — and the test writes a
    tag row by hand precisely because there is no endpoint that applies tags to a
    note, so the cascade claim could otherwise never be checked at all. After the
    delete: zero revisions and zero tag rows for that note id, while the other
    note's revision survives untouched.
    """
    seed, auth = await seeded_client(client, db_session)
    doomed = await _new_note(client, auth, title="Doomed")
    survivor = await _new_note(client, auth, title="Survivor")
    await _patch_note(client, auth, doomed["id"], content="doomed body")
    await _patch_note(client, auth, survivor["id"], content="survivor body")
    await _seed_tag_row(db_session, user_id=seed.owner.id, note_id=doomed["id"])

    assert await _revision_count(db_session, doomed["id"]) == 1
    assert await _tag_row_count(db_session, doomed["id"]) == 1

    deleted = await client.delete(f"{KNOWLEDGE}/notes/{doomed['id']}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    assert await _revision_count(db_session, doomed["id"]) == 0
    assert await _tag_row_count(db_session, doomed["id"]) == 0
    assert await _revision_count(db_session, survivor["id"]) == 1


async def test_another_accounts_note_answers_with_the_same_404_body_as_an_id_nobody_issued(
    client, db_session, assert_error_envelope
):
    """The refusal is identical for a stranger's row and for no row at all.

    Two different situations, one answer. A 403 would tell the caller the note
    exists and belongs to somebody else, which turns every id on this surface into
    a probe for whose notes are real; a different *message* would do the same
    thing just as effectively. The comparison is on the code and the message,
    not only on the status code, because "404 and not 403" is not satisfied by a
    404 that names the owner — and not on ``request_id``, which is per-request by
    design and would make two honest responses differ.
    """
    _, ada_auth = await seeded_client(client, db_session)
    _, grace_auth = await seeded_client(client, db_session, username="grace")
    grace_note = await _new_note(client, grace_auth)

    stranger = await client.get(f"{KNOWLEDGE}/notes/{grace_note['id']}", headers=ada_auth)
    nonexistent = await client.get(f"{KNOWLEDGE}/notes/{uuid.uuid4()}", headers=ada_auth)

    assert_error_envelope(stranger, status_code=404, code="not_found")
    assert_error_envelope(nonexistent, status_code=404, code="not_found")
    for key in ("code", "message", "details"):
        assert stranger.json()["error"][key] == nonexistent.json()["error"][key]
    assert stranger.json()["error"]["request_id"] != nonexistent.json()["error"]["request_id"]


async def test_a_deleted_note_stops_being_readable(client, db_session):
    """Deleting is real: the id answers 404 from then on, for its own owner too.

    The natural companion to the delete tests above, and the one that would catch
    a soft delete quietly implemented as a status change: a row the owner can
    still read after asking for it to be gone has not been deleted.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth)

    deleted = await client.delete(f"{KNOWLEDGE}/notes/{note['id']}", headers=auth)
    assert deleted.status_code == 204, deleted.text
    assert (await client.get(f"{KNOWLEDGE}/notes/{note['id']}", headers=auth)).status_code == 404


# ---------------------------------------------------------------------------
# Null is a value, not an absence
# ---------------------------------------------------------------------------


async def test_patching_a_note_title_to_null_is_a_422_and_writes_no_revision(client, db_session):
    """A ``NOT NULL`` column cannot be cleared, and the 422 comes before the history.

    Regression. ``NoteUpdate.title`` is ``str | None`` — it has to be, or omitting
    the key on a PATCH would fail validation — which also makes ``{"title":
    null}`` a valid payload. It then reached ``notes.title``, which is ``NOT
    NULL``, and the database answered with a ``NotNullViolation`` the service does
    not catch: a 500 with a traceback, for a request whose only fault is a missing
    pair of quotes. The refusal is checked *before* the snapshot on purpose — a
    revision describing an edit that failed would be a history entry for a state
    the note was never in.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="body")

    response = await _patch_note(client, auth, note["id"], title=None)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"

    assert await _revisions(db_session, note["id"]) == []
    unchanged = await client.get(f"{KNOWLEDGE}/notes/{note['id']}", headers=auth)
    assert unchanged.json()["title"] == "A note"


async def test_patching_a_note_body_to_null_is_a_422_rather_than_emptying_it(client, db_session):
    """Refused, not coerced to the empty string.

    The coercion is the tempting alternative and it is the wrong one: ``content``
    defaults to ``""`` on create, so writing ``""`` would answer "clear the body"
    with a note that reads as having never had one — the user's prose gone and no
    error shown. Refusing keeps the only honest options, retype the body or
    delete it.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="the prose")

    response = await _patch_note(client, auth, note["id"], content=None)
    assert response.status_code == 422, response.text

    unchanged = await client.get(f"{KNOWLEDGE}/notes/{note['id']}", headers=auth)
    assert unchanged.json()["content"] == "the prose"


async def test_clearing_a_summary_with_null_is_honoured(client, db_session):
    """``summary`` is nullable, so ``null`` there really does mean "clear it".

    The counterpart to the two tests above and the guard on over-refusing: a
    blanket "PATCH may not send null" rule would make a nullable column
    impossible to empty, and the documented way to clear one is ``"summary":
    null`` precisely because an absent key and a null must stay different.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="body", summary="a summary")

    response = await _patch_note(client, auth, note["id"], summary=None)
    assert response.status_code == 200, response.text
    assert response.json()["summary"] is None


async def test_renaming_a_concept_to_null_is_a_422_rather_than_a_conflict_that_never_happened(
    client, db_session
):
    """The duplicate-name handler must not become the answer to a null name.

    Regression of the worst kind of the three: ``update_concept`` catches
    ``IntegrityError`` to turn a genuine duplicate into a 409, and a null name
    raises the same error for the opposite reason. Unfixed, the caller is told it
    already has a concept called "None" — a confident statement about somebody
    else's data, made because of a missing quote.
    """
    _, auth = await seeded_client(client, db_session)
    created = await client.post(f"{KNOWLEDGE}/concepts", json={"name": "concurrency"}, headers=auth)
    assert created.status_code == 201, created.text

    response = await client.patch(
        f"{KNOWLEDGE}/concepts/{created.json()['id']}", json={"name": None}, headers=auth
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"

    # There is no GET /concepts/{id} on this surface, so the row is read back
    # through the listing the concept appears in.
    listed = await client.get(f"{KNOWLEDGE}/concepts", headers=auth)
    assert [item["name"] for item in listed.json()["items"]] == ["concurrency"]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def test_search_never_returns_another_accounts_notes(client, db_session):
    """A search term that matches both accounts returns only the caller's rows.

    Tested across two accounts rather than argued from the scoping: the same term,
    the same table, the same parameterised query, and Ada's result set must hold
    exactly her one note — Grace's note has to be absent from the body text
    entirely, not merely unrendered.
    """
    _, ada_auth = await seeded_client(client, db_session)
    _, grace_auth = await seeded_client(client, db_session, username="grace")
    ada_note = await _new_note(client, ada_auth, title="Shared term for Ada")
    grace_note = await _new_note(client, grace_auth, title="Shared term for Grace")

    response = await client.get(
        f"{KNOWLEDGE}/search", params={"q": "Shared term"}, headers=ada_auth
    )
    assert response.status_code == 200, response.text
    found = [item["id"] for item in response.json()["notes"]]
    assert found == [ada_note["id"]]
    assert str(grace_note["id"]) not in response.text


async def test_search_covers_bookmarks_as_well_as_the_other_kinds(client, db_session):
    """An unfiltered search looks at every kind a search may look in.

    Regression. The list of kinds was the *graph's* list — a bookmark is not a
    node, so it cannot be drawn — and the search inherited it, which left
    ``result.bookmarks`` permanently empty and the branch that fills it
    unreachable. The spec names bookmarks among the things knowledge search
    covers, the response model carries the field, and to a caller "no bookmark
    matched" was indistinguishable from "no bookmark was ever looked for" — a
    measured zero that looks exactly like an absence of measurement. One note and
    one bookmark both carrying the term: an unfiltered search must return both,
    and ``?type=note`` must narrow to the note and leave the bookmark group
    empty — which is a *measured* empty, because the filter asked it to be.
    """
    _, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, title="Pruner suffix notes")
    bookmark = await client.post(
        f"{KNOWLEDGE}/bookmarks",
        json={"url": "https://example.com/pruner", "title": "Pruner suffix article"},
        headers=auth,
    )
    assert bookmark.status_code == 201, bookmark.text

    response = await client.get(f"{KNOWLEDGE}/search", params={"q": "Pruner suffix"}, headers=auth)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["id"] for item in payload["notes"]] == [note["id"]]
    assert [item["url"] for item in payload["bookmarks"]] == ["https://example.com/pruner"]

    narrowed = await client.get(
        f"{KNOWLEDGE}/search", params={"q": "Pruner suffix", "type": "note"}, headers=auth
    )
    assert narrowed.status_code == 200, narrowed.text
    assert [item["id"] for item in narrowed.json()["notes"]] == [note["id"]]
    assert narrowed.json()["bookmarks"] == []


async def test_the_search_type_filter_accepts_every_kind_the_search_looks_in(client, db_session):
    """``?type=`` is as wide as the search itself, so no kind is a 422.

    Regression, and a worse shape than the one above. ``?type=`` was annotated
    with :class:`~app.models.enums.KnowledgeEntityType` — the *graph's* node
    types — while the service underneath it coerced through
    :func:`~app.services.knowledge_service._search_kind_or_none` and knew five
    kinds. FastAPI validated the query string before the handler was ever
    reached, so ``GET /knowledge/search?q=x&type=bookmark`` answered **422** for
    exactly the kind the same response fills in unfiltered: a filter that refuses
    the one value that would have been legal, and whose absence the unfiltered
    answer does nothing to reveal.

    Asserting every member of ``KnowledgeSearchKind`` is what keeps the router
    and the service from drifting apart again — the service side already had the
    wider set and said so, and nothing checked that the router agreed.
    """
    _, auth = await seeded_client(client, db_session)

    for kind in KnowledgeSearchKind:
        response = await client.get(
            f"{KNOWLEDGE}/search", params={"q": "Pruner suffix", "type": kind.value}, headers=auth
        )
        assert response.status_code == 200, f"{kind.value}: {response.text}"


async def test_the_search_type_filter_still_refuses_a_kind_no_search_covers(
    client, db_session, assert_error_envelope
):
    """``?type=category`` is a 422, because a category is a label with no text.

    The widening above is not "accept anything": a category holds no free text to
    match, so it is absent from the search kind vocabulary and asking for it is
    still the refusal it was. Both halves are asserted because only having one
    would let a regression hide in the other.
    """
    _, auth = await seeded_client(client, db_session)

    response = await client.get(
        f"{KNOWLEDGE}/search", params={"q": "Pruner suffix", "type": "category"}, headers=auth
    )

    assert_error_envelope(response, status_code=422, code="validation_error")


async def test_a_search_term_is_matched_literally_rather_than_as_a_wildcard(client, db_session):
    """A ``%`` or an ``_`` in the term looks for that character, not for anything.

    ``ILIKE`` treats both as wildcards, so a caller searching ``50%`` would get
    every note the user has written and a caller searching ``_`` would get every
    note of one character or more — the caller's string silently changing what the
    query means, and the answer indistinguishable from a real result. Four notes
    are seeded: one carrying ``%``, one carrying ``_``, and two carrying neither.
    ``50%`` returns exactly the first; ``_`` returns exactly the second.
    """
    _, auth = await seeded_client(client, db_session)
    percent = await _new_note(client, auth, title="50% of the way")
    underscore = await _new_note(client, auth, title="snake_case naming")
    plain = await _new_note(client, auth, title="a plain title")
    other = await _new_note(client, auth, title="another plain title")

    literal_percent = await client.get(f"{KNOWLEDGE}/search", params={"q": "50%"}, headers=auth)
    assert [item["id"] for item in literal_percent.json()["notes"]] == [percent["id"]]

    literal_underscore = await client.get(f"{KNOWLEDGE}/search", params={"q": "_"}, headers=auth)
    assert [item["id"] for item in literal_underscore.json()["notes"]] == [underscore["id"]]

    both_plain = {str(plain["id"]), str(other["id"])}
    assert both_plain.isdisjoint({item["id"] for item in literal_percent.json()["notes"]})
    assert both_plain.isdisjoint({item["id"] for item in literal_underscore.json()["notes"]})


async def test_a_blank_search_term_is_a_422(client, db_session, assert_error_envelope):
    """Whitespace is not a query.

    ``min_length=1`` on the router admits ``"   "``, which strips to nothing, and
    an empty ``ILIKE`` pattern would match every row the caller owns — the most
    expensive request the search endpoint accepts, returned as a plausible result.
    It is refused, and it is refused the same way a term over the length cap is.
    """
    _, auth = await seeded_client(client, db_session)
    response = await client.get(f"{KNOWLEDGE}/search", params={"q": "   "}, headers=auth)
    assert_error_envelope(response, status_code=422, code="validation_error")


# ---------------------------------------------------------------------------
# The markdown path: content as text, and dangerous URLs
# ---------------------------------------------------------------------------


async def test_note_content_containing_html_and_a_javascript_link_is_stored_and_returned_as_text(
    client, db_session
):
    """The API is not an HTML sink, and must not become one.

    Note content is Markdown the client renders, so the server's job is to hand
    the bytes back unchanged and to have no field that is "rendered output". The
    probe is a body carrying a ``<script>`` element, an ``<img onerror>`` and a
    ``[click](javascript:…)`` link — the three shapes that matter — and the
    assertion is that the round trip is byte-identical and that ``<script``
    appears exactly once in the response, inside the ``content`` field the caller
    sent it in. Sanitising here would be wrong: it would corrupt the note, and the
    renderer that displays it is the place that has to escape, because the note is
    the user's text and not the server's output.
    """
    _, auth = await seeded_client(client, db_session)
    hostile = (
        '<script>alert("xss")</script>\n'
        '<img src=x onerror="alert(1)">\n'
        "[click](javascript:alert(1))\n"
    )
    created = await _new_note(client, auth, title="Hostile <b>title</b>", content=hostile)
    assert created["content"] == hostile
    assert created["title"] == "Hostile <b>title</b>"

    fetched = await client.get(f"{KNOWLEDGE}/notes/{created['id']}", headers=auth)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["content"] == hostile
    assert fetched.text.count("<script") == 1
    assert fetched.json()["revision_count"] == 0


async def test_a_javascript_url_is_refused_for_a_bookmark_or_a_resource(client, db_session):
    """Only ``http`` and ``https`` may be stored, on create *and* on update.

    A bookmark list is rendered as links, so a stored ``javascript:`` URL is a
    stored script the next person to open the list runs — which is why the check
    is an allowlist of two rather than a denylist that has to be extended every
    time a scheme is invented. Both the create and the PATCH path are exercised,
    because a check that only runs on insert is a decoration on an update.
    """
    _, auth = await seeded_client(client, db_session)

    bookmark = await client.post(
        f"{KNOWLEDGE}/bookmarks", json={"url": "javascript:alert(1)"}, headers=auth
    )
    assert bookmark.status_code == 422, bookmark.text

    resource = await client.post(
        f"{KNOWLEDGE}/resources",
        json={"title": "A resource", "url": "javascript:alert(1)"},
        headers=auth,
    )
    assert resource.status_code == 422, resource.text

    saved = await client.post(
        f"{KNOWLEDGE}/bookmarks", json={"url": "https://example.com/page"}, headers=auth
    )
    assert saved.status_code == 201, saved.text
    moved = await client.patch(
        f"{KNOWLEDGE}/bookmarks/{saved.json()['id']}",
        json={"url": "data:text/html,<script>alert(1)</script>"},
        headers=auth,
    )
    assert moved.status_code == 422, moved.text


# ---------------------------------------------------------------------------
# The event feed the Phase 6 analytics read
# ---------------------------------------------------------------------------


async def test_the_note_lifecycle_records_the_events_the_analytics_feed_reads(client, db_session):
    """Writing a note records what happened, with field names rather than prose.

    Phase 6 counts knowledge activity from ``activity_events``, so this feed being
    true is a precondition of that phase rather than a nicety. One note is
    created, edited, published, archived, restored and rolled back — six writes,
    one event each, and no event for the read. Asserted as a count per type rather
    than as a list, because the feed's ordering is the database's to choose and
    the claim being tested is *which* facts were recorded, not which row happened
    to land first. The metadata for an edit is a sorted list of field *names*: a
    note's content is user prose and a feed is not the place to copy it twice.
    """
    seed, auth = await seeded_client(client, db_session)
    note = await _new_note(client, auth, content="first")
    await _patch_note(client, auth, note["id"], content="second")
    for action in ("publish", "archive", "restore"):
        response = await client.post(f"{KNOWLEDGE}/notes/{note['id']}/{action}", headers=auth)
        assert response.status_code == 200, response.text
    revision_id = await _revision_id(db_session, note["id"])
    restored = await client.post(
        f"{KNOWLEDGE}/notes/{note['id']}/restore-revision/{revision_id}",
        headers=auth,
    )
    assert restored.status_code == 200, restored.text

    result = await db_session.execute(
        select(ActivityLog.event_type).where(ActivityLog.user_id == seed.owner.id)
    )
    recorded = Counter(event for (event,) in result.all())
    assert recorded == Counter(
        {
            "note_created": 1,
            "note_updated": 1,
            "note_published": 1,
            "note_archived": 1,
            "note_restored": 1,
            "note_revision_restored": 1,
        }
    )

    metadata = await db_session.scalar(
        select(ActivityLog.metadata_).where(
            ActivityLog.user_id == seed.owner.id,
            ActivityLog.event_type == "note_updated",
        )
    )
    assert metadata == {"fields": ["content"]}
