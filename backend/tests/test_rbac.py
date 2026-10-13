"""Role-based access control, end to end through the permission gate.

**Every test here requires a live PostgreSQL and has NOT been executed.** They
are marked ``integration``.

``GET /api/v1/users/`` exists as a permission-system fixture: a route whose
refusal is observable, so ``ROLE_PERMISSIONS`` is not merely a map nothing ever
reads. The fail-closed case matters most — a role value that drifted out of
step with the map must be denied end to end, not merely by the unit test.

Note: every route in ``app/api/v1/users.py`` resolves its service through
``get_user_service``, which is currently wired as
``UserService(repository, audit=audit)`` while
:class:`app.services.user_service.UserService` accepts only ``repository``.
That mismatch raises ``TypeError`` inside the dependency and every request here
answers 500 ``internal_error``. These tests are written against the documented
behaviour and will fail until the wiring is corrected — see the handoff report.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.security import decode_token
from app.models.user import User

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

ADMIN_PERMISSIONS = [
    "analytics.read",
    "calendar.read",
    "calendar.write",
    "jarvis.use",
    "knowledge.read",
    "knowledge.write",
    "projects.read",
    "projects.write",
    "tasks.read",
    "tasks.write",
    "users.read",
    "users.write",
]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _subject(tokens: dict) -> uuid.UUID:
    """The user id a token pair was minted for."""
    claims = decode_token(tokens["access_token"], settings=get_settings())
    return uuid.UUID(claims.subject)


async def _user_id_for(db_session, email: str) -> uuid.UUID:
    result = await db_session.execute(select(User).where(User.email == email))
    return result.scalar_one().id


async def _promote(db_session, user_id: uuid.UUID, role: str) -> None:
    """Set the role directly, as an operator or a fixture would."""
    await db_session.execute(update(User).where(User.id == user_id).values(role=role))
    await db_session.commit()


async def _deactivate(db_session, user_id: uuid.UUID) -> None:
    await db_session.execute(update(User).where(User.id == user_id).values(is_active=False))
    await db_session.commit()


async def _register(client, payload: dict = ADA) -> dict:
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _login(client, payload: dict) -> dict:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


# -- The gate ----------------------------------------------------------------


async def test_an_administrator_may_list_the_accounts(client, db_session):
    await _register(client)
    tokens = await _login(client, ADA)
    await _promote(db_session, _subject(tokens), "admin")

    response = await client.get("/api/v1/users/", headers=_bearer(tokens["access_token"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["email"] for entry in body] == [ADA["email"]]
    assert set(body[0]) == {
        "id",
        "email",
        "username",
        "display_name",
        "avatar_url",
        "role",
        "permissions",
        "is_active",
        "is_verified",
        "created_at",
        "updated_at",
        "last_login_at",
    }


async def test_an_ordinary_user_is_refused_with_403_forbidden(client, assert_error_envelope):
    """The refusal must be ``forbidden``, not ``not_found`` and not a 500.

    An anonymous caller gets 401 — authentication runs before the permission
    check, so "you are not signed in" is never confused with "you may not".
    """
    await _register(client)
    tokens = await _login(client, ADA)

    response = await client.get("/api/v1/users/", headers=_bearer(tokens["access_token"]))

    assert_error_envelope(response, status_code=403, code="forbidden")


async def test_an_unknown_role_in_the_database_is_refused(
    client, db_session, assert_error_envelope
):
    """Fail-closed end to end.

    The row holds a role the map has never heard of. The permission lookup
    returns nothing for it, so the request is denied — and it is *denied*, not
    crashed: raising here would turn a data problem into a 500 for that user on
    every protected endpoint.
    """
    await _register(client)
    tokens = await _login(client, ADA)
    await _promote(db_session, _subject(tokens), "wizard")

    response = await client.get("/api/v1/users/", headers=_bearer(tokens["access_token"]))

    assert_error_envelope(response, status_code=403, code="forbidden")


async def test_an_anonymous_caller_is_refused_with_401(client, assert_error_envelope):
    response = await client.get("/api/v1/users/")

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["details"] is None


async def test_a_malformed_bearer_is_refused_with_401(client, assert_error_envelope):
    response = await client.get("/api/v1/users/", headers=_bearer("not.a.jwt"))

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_an_inactive_administrator_is_refused(client, db_session, assert_error_envelope):
    """Authentication runs before the permission check.

    An administrator who has been deactivated is not an administrator.
    """
    await _register(client)
    tokens = await _login(client, ADA)
    user_id = _subject(tokens)
    await _promote(db_session, user_id, "admin")
    await _deactivate(db_session, user_id)

    response = await client.get("/api/v1/users/", headers=_bearer(tokens["access_token"]))

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_the_legacy_superuser_flag_alone_does_not_grant_the_listing(
    client, db_session, assert_error_envelope
):
    """Both gates must pass, and the permission one is fail-closed.

    The row is a legacy ``is_superuser``, so the superuser dependency is
    satisfied — but the role is one the permission map has never heard of, so
    ``USERS_READ`` grants nothing and the request is denied. That is the whole
    shape of the gate: an unrecognised role is not an implicit grant.
    """
    await _register(client)
    tokens = await _login(client, ADA)
    user_id = _subject(tokens)
    await _promote(db_session, user_id, "wizard")
    await db_session.execute(update(User).where(User.id == user_id).values(is_superuser=True))
    await db_session.commit()

    response = await client.get("/api/v1/users/", headers=_bearer(tokens["access_token"]))

    assert_error_envelope(response, status_code=403, code="forbidden")


async def test_the_admin_listing_includes_an_account_with_an_unknown_role(client, db_session):
    """An unknown role is listed, with no permissions.

    An administrator sees the account; the row is returned with an empty
    permission list rather than being silently omitted.
    """
    await _register(client, ADA)
    await _register(client, GRACE)
    tokens = await _login(client, ADA)
    await _promote(db_session, _subject(tokens), "admin")
    await _promote(db_session, await _user_id_for(db_session, GRACE["email"]), "wizard")

    response = await client.get("/api/v1/users/", headers=_bearer(tokens["access_token"]))

    assert response.status_code == 200, response.text
    by_email = {entry["email"]: entry for entry in response.json()}
    assert by_email[GRACE["email"]]["permissions"] == []
    assert by_email[ADA["email"]]["permissions"] == ADMIN_PERMISSIONS


# -- Profile editing ---------------------------------------------------------


async def test_a_user_may_edit_their_own_profile(client):
    """Every role may edit its own profile.

    ``USERS_WRITE`` is held by every role; ownership is implicit, because the
    route has no path parameter.
    """
    await _register(client)
    tokens = await _login(client, ADA)

    response = await client.patch(
        "/api/v1/users/me",
        json={"display_name": "Ada L.", "avatar_url": "https://cdn.example.com/ada.png"},
        headers=_bearer(tokens["access_token"]),
    )

    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Ada L."
    assert response.json()["avatar_url"] == "https://cdn.example.com/ada.png"


async def test_a_profile_edit_cannot_change_the_email_or_the_password(
    client, db_session, assert_error_envelope
):
    """Both are identity or credential transitions with rules of their own.

    A 422 is only half the claim. What actually matters is that the account is
    untouched afterwards: a handler that validated the body, wrote the password
    anyway and *then* reported the error would satisfy a status-code-only test
    and still have handed the account over. So the row is re-read and both
    credentials are tried against sign-in, which is the only way to observe the
    stored hash from outside.
    """
    await _register(client)
    tokens = await _login(client, ADA)
    user_id = _subject(tokens)
    before = (await db_session.get(User, user_id)).hashed_password

    response = await client.patch(
        "/api/v1/users/me",
        json={"email": "attacker@nexus.dev", "password": "Hijack-9-9"},
        headers=_bearer(tokens["access_token"]),
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    fields = {entry["field"] for entry in error["details"]["errors"]}
    assert fields == {"email", "password"}

    # ``populate_existing`` because the edit ran on the request's own session:
    # the row this test session already holds was never expired, so a plain
    # ``get`` would hand back the pre-edit values and prove nothing.
    after = await db_session.get(User, user_id, populate_existing=True)
    assert after.email == ADA["email"], "the address must survive a rejected edit"
    assert after.hashed_password == before, "a rejected edit must not rewrite the credential"

    assert (
        await client.post("/api/v1/auth/login", json={**ADA, "password": ADA["password"]})
    ).status_code == 200
    assert_error_envelope(
        await client.post(
            "/api/v1/auth/login", json={"email": ADA["email"], "password": "Hijack-9-9"}
        ),
        status_code=401,
        code="unauthorized",
    )


async def test_a_profile_edit_cannot_steal_a_username_that_is_taken(client, assert_error_envelope):
    await _register(client, ADA)
    await _register(client, GRACE)
    tokens = await _login(client, ADA)

    response = await client.patch(
        "/api/v1/users/me",
        json={"username": GRACE["username"]},
        headers=_bearer(tokens["access_token"]),
    )

    assert_error_envelope(response, status_code=409, code="conflict")


async def test_a_profile_edit_requires_a_bearer_token(client, assert_error_envelope):
    await _register(client)

    response = await client.patch("/api/v1/users/me", json={"display_name": "Nobody"})

    assert_error_envelope(response, status_code=401, code="unauthorized")
