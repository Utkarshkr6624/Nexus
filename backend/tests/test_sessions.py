"""Device sessions: listing, revoking one, revoking all — and the IDOR guard.

**Every test here requires a live PostgreSQL.** They are marked ``integration``
and they run by default: ``pytest.ini``'s ``addopts`` is
``-ra --strict-markers --strict-config``, with no ``-m`` filter, so the marker
names the precondition rather than excluding the tests. The database is the
suite's own — ``conftest.py`` creates the ``TEST_DATABASE_URL`` target, migrates
it to ``head`` and holds an advisory lock on it — so a run without one stops
rather than quietly skipping.

The centrepiece is :func:`test_revoking_another_users_session_answers_404_not_403`:
ownership is scoped inside the query rather than checked afterwards, so another
user's session is indistinguishable from one that never existed. A 403 would
confirm the id is real.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import decode_token, hash_token
from app.models.audit import AuditEvent, AuditLog
from app.models.session import Session

pytestmark = pytest.mark.integration

ADA = {
    "username": "ada",
    "email": "ada@nexus.dev",
    "password": "Correct-Horse-7",
}
GRACE = {
    "username": "grace",
    "email": "grace@nexus.dev",
    "password": "Another-Strong-Pass-9",
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _subject(pair: dict) -> uuid.UUID:
    """The user id a token pair was minted for."""
    claims = decode_token(pair["access_token"], settings=get_settings())
    return uuid.UUID(claims.subject)


async def _account(client, payload: dict) -> dict:
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _sign_in(client, payload: dict) -> dict:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _sessions_for(db_session, user_id: uuid.UUID) -> dict[str, Session]:
    result = await db_session.execute(
        select(Session).where(Session.user_id == user_id).order_by(Session.created_at)
    )
    return {str(row.id): row for row in result.scalars().all()}


# -- Listing -----------------------------------------------------------------


async def test_the_list_contains_only_the_callers_own_sessions(client):
    await _account(client, ADA)
    await _account(client, GRACE)
    ada_session = await _sign_in(client, ADA)
    await _sign_in(client, GRACE)

    response = await client.get(
        "/api/v1/auth/sessions", headers=_bearer(ada_session["access_token"])
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body["sessions"]] == [ada_session["session_id"]]
    assert body["current_id"] == ada_session["session_id"]


async def test_the_listing_marks_exactly_one_row_as_current(client):
    await _account(client, ADA)
    first = await _sign_in(client, ADA)
    second = await _sign_in(client, ADA)

    response = await client.get("/api/v1/auth/sessions", headers=_bearer(second["access_token"]))

    body = response.json()
    assert body["current_id"] == second["session_id"]
    current = [row["id"] for row in body["sessions"] if row["is_current"]]
    assert current == [second["session_id"]]
    assert {row["id"] for row in body["sessions"]} == {
        first["session_id"],
        second["session_id"],
    }


async def test_a_session_row_never_exposes_its_owner_or_its_digest(client):
    """A session row carries neither its owner nor its digest.

    Every row already belongs to the caller, so neither field tells the client
    anything — and ``token_hash`` is still a digest an attacker could compare.
    """
    await _account(client, ADA)
    tokens = await _sign_in(client, ADA)

    response = await client.get("/api/v1/auth/sessions", headers=_bearer(tokens["access_token"]))

    row = response.json()["sessions"][0]
    assert set(row) == {
        "id",
        "user_agent",
        "ip_address",
        "created_at",
        "last_used_at",
        "expires_at",
        "revoked_at",
        "is_current",
    }
    assert hash_token(tokens["refresh_token"]) not in response.text
    assert "user_id" not in response.text


async def test_the_listing_requires_a_bearer_token(client, assert_error_envelope):
    response = await client.get("/api/v1/auth/sessions")

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_the_listing_omits_a_session_that_has_been_revoked(client):
    """The listing is of live sign-ins; a revoked row is history, not a device."""
    await _account(client, ADA)
    keep = await _sign_in(client, ADA)
    drop = await _sign_in(client, ADA)
    await client.delete(
        f"/api/v1/auth/sessions/{drop['session_id']}",
        headers=_bearer(keep["access_token"]),
    )

    response = await client.get("/api/v1/auth/sessions", headers=_bearer(keep["access_token"]))

    assert [row["id"] for row in response.json()["sessions"]] == [keep["session_id"]]


# -- Revoking one ------------------------------------------------------------


async def test_revoking_one_of_your_own_sessions_answers_204(client, db_session):
    await _account(client, ADA)
    keep = await _sign_in(client, ADA)
    drop = await _sign_in(client, ADA)

    response = await client.delete(
        f"/api/v1/auth/sessions/{drop['session_id']}",
        headers=_bearer(keep["access_token"]),
    )

    assert response.status_code == 204
    assert response.content == b""
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(keep["access_token"]))
    ).status_code == 200

    revoked = await db_session.get(Session, uuid.UUID(drop["session_id"]))
    assert revoked.revoked_at is not None
    assert revoked.token_hash == hash_token(drop["refresh_token"]), "the row is kept, not deleted"


async def test_a_revoked_session_can_no_longer_be_refreshed(client, assert_error_envelope):
    await _account(client, ADA)
    keep = await _sign_in(client, ADA)
    drop = await _sign_in(client, ADA)
    await client.delete(
        f"/api/v1/auth/sessions/{drop['session_id']}",
        headers=_bearer(keep["access_token"]),
    )

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": drop["refresh_token"]}
    )

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_revoking_a_session_twice_is_not_an_error(client, db_session):
    """A double-click is not a failure, and the first revocation time is kept."""
    await _account(client, ADA)
    keep = await _sign_in(client, ADA)
    drop = await _sign_in(client, ADA)

    first = await client.delete(
        f"/api/v1/auth/sessions/{drop['session_id']}",
        headers=_bearer(keep["access_token"]),
    )
    revoked_at = (await db_session.get(Session, uuid.UUID(drop["session_id"]))).revoked_at
    second = await client.delete(
        f"/api/v1/auth/sessions/{drop['session_id']}",
        headers=_bearer(keep["access_token"]),
    )

    assert first.status_code == second.status_code == 204
    assert (await db_session.get(Session, uuid.UUID(drop["session_id"]))).revoked_at == revoked_at


# -- The IDOR guard ----------------------------------------------------------


async def test_revoking_another_users_session_answers_404_not_403(
    client, db_session, assert_error_envelope
):
    """The security-critical assertion in this file.

    Ownership is part of the *lookup*, so a session belonging to somebody else
    never resolves and the endpoint cannot be used to probe which session ids
    are real. A 403 would confirm the id exists; only a 404 is indistinguishable
    from an id that was never issued. The other user's session must also still
    be live afterwards — a 404 that revoked it anyway would be worse.
    """
    await _account(client, ADA)
    await _account(client, GRACE)
    attacker = await _sign_in(client, ADA)
    victim = await _sign_in(client, GRACE)

    response = await client.delete(
        f"/api/v1/auth/sessions/{victim['session_id']}",
        headers=_bearer(attacker["access_token"]),
    )

    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert response.status_code != 403
    assert error["details"] is None

    untouched = await db_session.get(Session, uuid.UUID(victim["session_id"]))
    assert untouched.revoked_at is None, "another user's session must survive the attempt"
    assert untouched.user_id == _subject(victim)
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(victim["access_token"]))
    ).status_code == 200


async def test_a_session_id_that_never_existed_answers_the_same_404(client, assert_error_envelope):
    """The two cases must be indistinguishable, or the difference is the oracle."""
    await _account(client, ADA)
    await _account(client, GRACE)
    attacker = await _sign_in(client, ADA)
    victim = await _sign_in(client, GRACE)

    somebody_elses = await client.delete(
        f"/api/v1/auth/sessions/{victim['session_id']}",
        headers=_bearer(attacker["access_token"]),
    )
    never_existed = await client.delete(
        f"/api/v1/auth/sessions/{uuid.uuid4()}",
        headers=_bearer(attacker["access_token"]),
    )

    assert somebody_elses.status_code == never_existed.status_code == 404
    assert (
        somebody_elses.json()["error"]["code"]
        == never_existed.json()["error"]["code"]
        == "not_found"
    )
    assert somebody_elses.json()["error"]["message"] == never_existed.json()["error"]["message"]


async def test_revoking_a_session_requires_a_bearer_token(client, assert_error_envelope):
    await _account(client, ADA)
    tokens = await _sign_in(client, ADA)

    response = await client.delete(f"/api/v1/auth/sessions/{tokens['session_id']}")

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_a_malformed_session_id_is_a_validation_error(client, assert_error_envelope):
    await _account(client, ADA)
    tokens = await _sign_in(client, ADA)

    response = await client.delete(
        "/api/v1/auth/sessions/not-a-uuid", headers=_bearer(tokens["access_token"])
    )

    assert_error_envelope(response, status_code=422, code="validation_error")


# -- Sign out everywhere -----------------------------------------------------


async def test_logout_all_revokes_every_other_session(client, db_session):
    """The caller's own device stays signed in.

    The endpoint is "sign out my *other* devices"; sparing the caller by the
    ``sid`` claim is what makes it usable without an immediate re-login.
    """
    await _account(client, ADA)
    keep = await _sign_in(client, ADA)
    first_other = await _sign_in(client, ADA)
    second_other = await _sign_in(client, ADA)

    response = await client.post("/api/v1/auth/logout-all", headers=_bearer(keep["access_token"]))

    assert response.status_code == 204
    assert response.content == b""

    rows = await _sessions_for(db_session, _subject(keep))
    assert rows[str(uuid.UUID(keep["session_id"]))].revoked_at is None
    assert rows[str(uuid.UUID(first_other["session_id"]))].revoked_at is not None
    assert rows[str(uuid.UUID(second_other["session_id"]))].revoked_at is not None


async def test_logout_all_leaves_other_accounts_alone(client, db_session):
    await _account(client, ADA)
    await _account(client, GRACE)
    ada = await _sign_in(client, ADA)
    grace = await _sign_in(client, GRACE)

    response = await client.post("/api/v1/auth/logout-all", headers=_bearer(ada["access_token"]))

    assert response.status_code == 204
    survivor = await db_session.get(Session, uuid.UUID(grace["session_id"]))
    assert survivor.user_id == _subject(grace)
    assert survivor.revoked_at is None


async def test_logout_all_requires_a_bearer_token(client, assert_error_envelope):
    response = await client.post("/api/v1/auth/logout-all")

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_logout_all_writes_a_sessions_revoked_all_row(client, db_session):
    await _account(client, ADA)
    keep = await _sign_in(client, ADA)
    await _sign_in(client, ADA)

    await client.post("/api/v1/auth/logout-all", headers=_bearer(keep["access_token"]))

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.event_type == AuditEvent.SESSIONS_REVOKED_ALL.value)
    )
    row = result.scalar_one()
    assert row.metadata_["revoked"] == 1
    assert row.metadata_["kept"] == keep["session_id"]


# -- Rotation ----------------------------------------------------------------


async def test_rotation_keeps_the_same_session_row(client, db_session):
    """One row is one device: rotating a hundred times still shows one device."""
    await _account(client, ADA)
    first = await _sign_in(client, ADA)

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )

    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["session_id"] == first["session_id"]
    assert rotated.json()["refresh_token"] != first["refresh_token"]

    row = await db_session.get(Session, uuid.UUID(first["session_id"]))
    assert row.revoked_at is None
    assert row.token_hash != hash_token(first["refresh_token"]), "the digest is replaced in place"


# -- The session cap ---------------------------------------------------------


async def test_signing_in_beyond_the_cap_evicts_the_oldest_session(client, db_session, monkeypatch):
    """The oldest session is evicted once the cap is reached.

    A stolen refresh token replayed over and over would otherwise pile up
    sessions at the attacker's convenience, with the owner seeing nothing
    unusual. Evicting from the front treats the attacker and the owner alike.
    """
    monkeypatch.setenv("MAX_ACTIVE_SESSIONS", "2")
    get_settings.cache_clear()

    await _account(client, ADA)
    oldest = await _sign_in(client, ADA)
    middle = await _sign_in(client, ADA)
    newest = await _sign_in(client, ADA)

    rows = await _sessions_for(db_session, _subject(oldest))
    assert rows[str(uuid.UUID(oldest["session_id"]))].revoked_at is not None
    assert rows[str(uuid.UUID(middle["session_id"]))].revoked_at is None
    assert rows[str(uuid.UUID(newest["session_id"]))].revoked_at is None


async def test_the_newest_session_can_never_evict_itself(client, db_session, monkeypatch):
    """A brand-new session never evicts itself.

    ``created_at`` has one-second resolution, so two sign-ins in the same
    second tie; a tie must never let a brand-new session evict itself.
    """
    monkeypatch.setenv("MAX_ACTIVE_SESSIONS", "1")
    get_settings.cache_clear()

    await _account(client, ADA)
    first = await _sign_in(client, ADA)
    second = await _sign_in(client, ADA)

    rows = await _sessions_for(db_session, _subject(first))
    assert rows[str(uuid.UUID(first["session_id"]))].revoked_at is not None
    assert rows[str(uuid.UUID(second["session_id"]))].revoked_at is None
