"""The audit trail has to answer questions, not just count rows.

Every defect in this file is the same shape: nothing errors, nothing 500s, and
the symptom is a record that reads as complete while answering nothing. A feed
that lists which fields changed cannot show what they changed from. A
``?task_id=`` filter that matches a column the one event about that task cannot
use returns ``total: 0``. A dependency edge whose other end was deleted leaves
the waiting card's history reading as though the blocker were still there.

What this file pins
-------------------
* **Old values.** ``task_updated`` / ``project_updated`` carry what each changed
  field held *before* the write, so an edit can be shown and reversed from the
  feed rather than only listed by name.
* **The priority contract is true.** The detail PATCH accepts ``priority`` and
  ``TaskPriorityChange`` used to claim no client could get one through it; the
  feed now reports either route as ``task_priority_changed`` with the old and the
  new grade.
* **A deleted task is still findable.** ``task_deleted`` cannot carry the
  ``task_id`` column (the row it would point at has just been deleted), so the
  documented ``?task_id=`` filter reads the id out of ``metadata``.
* **Un-blocking a dependent is recorded.** Deleting a prerequisite flips the
  dependent's ``has_blocked_dependencies`` to false; the dependent's own history
  now says so.
* **An edit cannot manufacture a cross-project edge.** ``POST /dependencies``
  refuses one, so ``PATCH {"project_id": …}`` refuses one too.
* **A dependency edge is readable from both ends**, and a direction that is not a
  direction is a 422 rather than a false ``200 []``.
* **A block reason reaches the feed** — the global one and the project-scoped one
  are the same rows, which is asserted rather than assumed.

House style, following ``tests/test_task_project_api.py``
----------------------------------------------------------
``pytestmark = pytest.mark.integration``, everything driven through the real HTTP
API with :func:`tests.analytics_fixtures.seeded_client`, and every expected
figure argued in the test's own docstring rather than recorded from a run.
"""

from __future__ import annotations

import pytest

from tests.analytics_fixtures import seeded_client

pytestmark = pytest.mark.integration


async def _new_project(client, auth, name: str = "Trail") -> str:
    response = await client.post("/api/v1/projects", json={"name": name}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _new_task(client, auth, project_id: str, title: str, **extra) -> str:
    response = await client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": title, **extra},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _feed(client, auth, query: str = "") -> list[dict]:
    """The caller's whole feed, newest first, as a list of raw event objects."""
    response = await client.get(f"/api/v1/activity?limit=100{query}", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _of_type(events: list[dict], event_type: str) -> list[dict]:
    return [event for event in events if event["event_type"] == event_type]


# ---------------------------------------------------------------------------
# Old values, and the priority contract
# ---------------------------------------------------------------------------


async def test_a_task_edit_records_the_value_each_field_held_before_the_write(client, db_session):
    """``task_updated`` says what was overwritten, not only what was touched.

    A card created at ``low`` / 30 minutes and edited to ``critical`` / 90 has one
    history entry that used to read ``{"fields": [...]}``: enough to know an edit
    happened and not enough to show it or undo it. The same entry now carries
    ``changes`` with the old and the new value of every field that actually moved.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task = await _new_task(client, auth, project, "Re-triage me", priority="low")

    edited = await client.patch(
        f"/api/v1/tasks/{task}",
        json={"priority": "critical", "estimated_minutes": 90},
        headers=auth,
    )
    assert edited.status_code == 200, edited.text

    updates = _of_type(await _feed(client, auth, f"&task_id={task}"), "task_updated")
    assert len(updates) == 1, updates
    changes = updates[0]["metadata"]["changes"]
    assert changes == {
        "estimated_minutes": {"from": None, "to": 90},
        "priority": {"from": "low", "to": "critical"},
    }, changes
    # The field names are still there: a client that only wants to know *what*
    # was touched should not have to read the values to find out.
    assert updates[0]["metadata"]["fields"] == ["estimated_minutes", "priority"]


async def test_a_task_edit_that_changes_nothing_records_no_change_entries(client, db_session):
    """Sending a field back unchanged is not a change.

    ``fields`` lists what the client *named*; ``changes`` lists what actually
    moved. A PATCH that echoes the card's own title produces one entry with no
    ``changes`` at all, rather than a feed full of ``from == to`` pairs.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task = await _new_task(client, auth, project, "Same title")

    edited = await client.patch(
        f"/api/v1/tasks/{task}", json={"title": "Same title"}, headers=auth
    )
    assert edited.status_code == 200, edited.text

    updates = _of_type(await _feed(client, auth, f"&task_id={task}"), "task_updated")
    assert updates[0]["metadata"]["fields"] == ["title"]
    assert updates[0]["metadata"]["changes"] == {}


async def test_a_priority_sent_to_the_detail_patch_is_reported_as_its_own_event(client, db_session):
    """The dedicated event fires whichever route carried the priority.

    ``TaskPriorityChange`` claimed a client could not get a priority change
    through a generic edit even if it tried, while ``TaskUpdate`` has carried
    ``priority`` since the first commit: the edit answered 200, changed the
    column, and wrote only ``task_updated``. Both routes now write
    ``task_priority_changed`` carrying the old and the new grade, so "when was
    this de-prioritised" has one answer whichever door the client used.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    via_patch = await _new_task(client, auth, project, "Through the detail PATCH")
    via_route = await _new_task(client, auth, project, "Through the priority route")

    patched = await client.patch(
        f"/api/v1/tasks/{via_patch}", json={"priority": "high"}, headers=auth
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["priority"] == "high"

    dedicated = await client.patch(
        f"/api/v1/tasks/{via_route}/priority", json={"priority": "high"}, headers=auth
    )
    assert dedicated.status_code == 200, dedicated.text

    events = await _feed(client, auth)
    changes = _of_type(events, "task_priority_changed")
    assert {event["task_id"] for event in changes} == {via_patch, via_route}, changes
    assert all(event["metadata"] == {"from": "medium", "to": "high"} for event in changes)


async def test_a_project_edit_records_the_value_each_field_held_before_the_write(client, db_session):
    """The project edit event says the same thing the task one does."""
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth, "Old name")

    edited = await client.patch(
        f"/api/v1/projects/{project}", json={"name": "New name"}, headers=auth
    )
    assert edited.status_code == 200, edited.text

    updates = _of_type(await _feed(client, auth, f"&project_id={project}"), "project_updated")
    assert updates[0]["metadata"]["changes"] == {
        "name": {"from": "Old name", "to": "New name"}
    }


# ---------------------------------------------------------------------------
# A deleted task is still findable, and its dependents are told
# ---------------------------------------------------------------------------


async def test_the_task_filter_finds_the_event_about_a_task_that_no_longer_exists(
    client, db_session
):
    """``?task_id=`` answers for ``task_deleted`` too.

    ``activity_events.task_id`` is ``ON DELETE SET NULL``, so the deletion event
    is written after the row is gone and cannot carry the column — the id travels
    in ``metadata``. The documented filter read only the column, so the one event
    that exists to answer "what happened to that card?" was the one event the
    filter could not return: ``total: 0`` for a task whose whole history was one
    deletion.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task = await _new_task(client, auth, project, "Delete me")

    deleted = await client.delete(f"/api/v1/tasks/{task}", headers=auth)
    assert deleted.status_code == 204, deleted.text

    response = await client.get(f"/api/v1/activity?task_id={task}", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    print("DEBUG", body)
    print("DEBUG-ALL", await _feed(client, auth))
    assert body["meta"]["total"] == 2, body
    kinds = [item["event_type"] for item in body["items"]]
    assert kinds == ["task_deleted", "task_created"], kinds
    assert body["items"][0]["metadata"]["task_id"] == task


async def test_deleting_a_prerequisite_leaves_a_trail_on_the_card_it_was_blocking(
    client, db_session
):
    """Un-blocking a dependent is recorded on the dependent.

    Deleting a prerequisite cascades the edge, so the waiting card's
    ``has_blocked_dependencies`` goes true → false and its next completion is
    allowed. That is right — there is nothing left to wait for — and it used to be
    silent: the dependent's own history read as though the blocker were still
    there, so "why could I not finish this yesterday?" had no answer anywhere.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    blocker = await _new_task(client, auth, project, "The prerequisite")
    waiting = await _new_task(client, auth, project, "The waiting card")

    edge = await client.post(
        f"/api/v1/tasks/{waiting}/dependencies?depends_on_id={blocker}", headers=auth
    )
    assert edge.status_code == 201, edge.text
    before = await client.get(f"/api/v1/tasks/{waiting}", headers=auth)
    assert before.json()["has_blocked_dependencies"] is True

    assert (await client.delete(f"/api/v1/tasks/{blocker}", headers=auth)).status_code == 204

    after = await client.get(f"/api/v1/tasks/{waiting}", headers=auth)
    assert after.json()["has_blocked_dependencies"] is False

    updates = _of_type(await _feed(client, auth, f"&task_id={waiting}"), "task_updated")
    removed = [
        event
        for event in updates
        if event["metadata"].get("removed_by") == "task_deleted"
    ]
    assert len(removed) == 1, updates
    assert removed[0]["metadata"]["removed_dependency"] == blocker
    assert removed[0]["metadata"]["depends_on_title"] == "The prerequisite"


# ---------------------------------------------------------------------------
# Edits cannot manufacture the state the API refuses to build
# ---------------------------------------------------------------------------


async def test_a_task_cannot_be_moved_to_another_project_over_a_dependency_edge(
    client, db_session
):
    """The same-project rule is enforced on the move, not only on the create.

    ``POST /tasks/{id}/dependencies`` answers 422 — "A dependency must be on a task
    in the same project" — for an edge whose ends straddle two projects. A detail
    PATCH carrying ``project_id`` could produce exactly that: the move returned
    200, the card ended up blocked on work living on another board, and the only
    repair was finding the edge by hand and deleting it.
    """
    _seed, auth = await seeded_client(client, db_session)
    here = await _new_project(client, auth, "Here")
    there = await _new_project(client, auth, "There")
    blocker = await _new_task(client, auth, here, "Stays put")
    waiting = await _new_task(client, auth, here, "Wants to move")

    edge = await client.post(
        f"/api/v1/tasks/{waiting}/dependencies?depends_on_id={blocker}", headers=auth
    )
    assert edge.status_code == 201, edge.text

    moved = await client.patch(
        f"/api/v1/tasks/{waiting}", json={"project_id": there}, headers=auth
    )
    assert moved.status_code == 422, moved.text
    assert "dependency" in moved.json()["error"]["message"].lower()

    # The refusal left the card where it was, so the edge is still a legal one.
    unchanged = await client.get(f"/api/v1/tasks/{waiting}", headers=auth)
    assert unchanged.json()["project_id"] == here


async def test_a_task_with_no_dependency_edge_can_still_be_moved(client, db_session):
    """The guard refuses a move that would break an edge, and only that one.

    A rule that also refused ordinary moves would be a bug of its own, so the
    permissive case is pinned next to the refusal.
    """
    _seed, auth = await seeded_client(client, db_session)
    here = await _new_project(client, auth, "Here")
    there = await _new_project(client, auth, "There")
    card = await _new_task(client, auth, here, "Free to move")

    moved = await client.patch(
        f"/api/v1/tasks/{card}", json={"project_id": there}, headers=auth
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["project_id"] == there


# ---------------------------------------------------------------------------
# A dependency edge is readable from both ends
# ---------------------------------------------------------------------------


async def test_the_dependency_listing_answers_both_directions(client, db_session):
    """``direction`` is honoured, and an unknown one is a 422.

    An undeclared query parameter is discarded by the router before the handler
    runs, so ``?direction=reverse`` used to answer ``200 []`` for a card with
    three cards downstream of it — a request that named a direction the API did
    not have, answered as though the graph were empty.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    root = await _new_task(client, auth, project, "Root")
    blockers = [await _new_task(client, auth, project, f"Blocker {index}") for index in range(2)]
    for blocker in blockers:
        edge = await client.post(
            f"/api/v1/tasks/{root}/dependencies?depends_on_id={blocker}", headers=auth
        )
        assert edge.status_code == 201, edge.text

    forward = await client.get(f"/api/v1/tasks/{root}/dependencies", headers=auth)
    assert forward.status_code == 200, forward.text
    assert {task["title"] for task in forward.json()} == {"Blocker 0", "Blocker 1"}

    reverse = await client.get(
        f"/api/v1/tasks/{root}/dependencies?direction=reverse", headers=auth
    )
    assert reverse.status_code == 200, reverse.text
    assert reverse.json() == [], reverse.text

    # And from the other end, the same card is what the root is waiting on.
    downstream = await client.get(
        f"/api/v1/tasks/{blockers[0]}/dependencies?direction=dependents", headers=auth
    )
    assert downstream.status_code == 200, downstream.text
    assert [task["title"] for task in downstream.json()] == ["Root"]

    unknown = await client.get(
        f"/api/v1/tasks/{root}/dependencies?direction=sideways", headers=auth
    )
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["error"]["details"]["field"] == "direction"


# ---------------------------------------------------------------------------
# A block reason reaches the feed, from either reading of it
# ---------------------------------------------------------------------------


async def test_a_block_reason_is_in_the_feed_and_the_project_reads_the_same_row(
    client, db_session
):
    """The global feed and the project feed are the same rows, note included.

    The schema promises the ``note`` is "recorded on the resulting activity
    event", and one tester saw it in the project-scoped feed and not in the
    global one. Both are ``ActivityService.feed`` with a different predicate, so
    there is no row that appears in one and not the other — asserted here so the
    question has a checked answer rather than a plausible one.
    """
    _seed, auth = await seeded_client(client, db_session)
    project = await _new_project(client, auth)
    task = await _new_task(client, auth, project, "Waiting on the vendor")

    blocked = await client.post(
        f"/api/v1/tasks/{task}/block", json={"note": "waiting on the vendor"}, headers=auth
    )
    assert blocked.status_code == 200, blocked.text

    global_events = _of_type(await _feed(client, auth, f"&task_id={task}"), "task_blocked")
    scoped = await client.get(f"/api/v1/projects/{project}/activity?limit=100", headers=auth)
    assert scoped.status_code == 200, scoped.text
    project_events = _of_type(scoped.json()["items"], "task_blocked")

    assert len(global_events) == 1, global_events
    assert global_events[0]["metadata"]["note"] == "waiting on the vendor"
    assert project_events == global_events
