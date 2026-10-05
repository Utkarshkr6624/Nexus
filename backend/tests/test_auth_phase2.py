"""Registration, sign-in, password change and password reset — the Phase 2 wire contract.

**Every test here requires a live PostgreSQL and has NOT been executed.** They
are marked ``integration`` and are excluded from
``pytest -m "not integration"``. Nothing in this file is a claim about observed
behaviour; it is a specification the first machine with a database should
execute verbatim.

The fixtures used are the ones ``conftest.py`` already provides — ``client``
(which brings ``engine`` and ``truncated_database``), ``db_session`` for
reading rows the API wrote, and ``assert_error_envelope`` for the error shape.
No new infrastructure is introduced.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.core.config import Settings
from app.core.security import decode_token, hash_token
from app.models.audit import AuditEvent, AuditLog
from app.models.password_reset import PasswordResetToken
from app.models.session import Session
from app.models.user import User

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def expose_reset_token(monkeypatch: pytest.MonkeyPatch):
    """Let this module read the raw reset token the API hands back.

    The endpoint withholds it by default: a token returned for a known address
    and withheld for an unknown one is an account oracle, and redeeming it is an
    unauthenticated takeover. The tests below assert the behaviour of the reset
    FLOW, which cannot be exercised at all without a raw token, so they opt in
    explicitly rather than relying on a default. ``test_the_token_is_withheld_unless_the_install_asks_for_it``
    pins the default itself.

    ``get_settings`` is cached, so the environment change is only visible after
    the cache is cleared — and it is cleared again on the way out so the next
    module does not inherit the opt-in.
    """
    from app.core.config import get_settings

    monkeypatch.setenv("DEV_EXPOSE_RESET_TOKEN", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

ADA = {
    "username": "ada",
    "email": "ada@nexus.dev",
    "password": "Correct-Horse-7",
    "display_name": "Ada Lovelace",
}
GRACE = {
    "username": "grace",
    "email": "grace@nexus.dev",
    "password": "Another-Strong-Pass-9",
}

#: The one message both uniqueness refusals answer with, restated here rather
#: than imported so a change to the wording is a visible edit to these tests
#: rather than a silent one that follows the implementation.
_TAKEN_MESSAGE = "An account with this email or username already exists."
NEW_PASSWORD = "Rotated-Horse-42"

#: Passwords carried by the redemption attempts that must all be refused, so
#: that "refused" can be shown to mean "never written" rather than "written and
#: then reported as an error".
NEVER_APPLIED_ONE = "Never-Applied-One-11"
NEVER_APPLIED_TWO = "Never-Applied-Two-22"

USER_PERMISSIONS = [
    "analytics.read",
    "calendar.read",
    "calendar.write",
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


def _claims(token: str, settings: Settings) -> dict:
    return decode_token(token, settings=settings).claims


async def _register(client, payload: dict = ADA) -> dict:
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _login(client, payload: dict = ADA) -> dict:
    response = await client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _sessions_for(db_session, user_id: uuid.UUID) -> list[Session]:
    result = await db_session.execute(
        select(Session).where(Session.user_id == user_id).order_by(Session.created_at)
    )
    return list(result.scalars().all())


# -- Registration ------------------------------------------------------------


async def test_register_returns_the_public_user_representation(client):
    user = await _register(client)

    assert set(user) == {
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
    assert user["email"] == ADA["email"]
    assert user["username"] == ADA["username"]
    assert user["display_name"] == "Ada Lovelace"
    assert user["role"] == "user"
    assert user["is_active"] is True
    assert user["last_login_at"] is None
    # The role is now the single answer to "what may this account do", so the
    # superseded ``is_superuser`` flag is gone from the wire entirely.
    assert "is_superuser" not in user


async def test_register_never_echoes_credential_material(client):
    user = await _register(client)

    assert "hashed_password" not in user
    assert "password" not in user
    assert ADA["password"] not in str(user)


async def test_register_publishes_the_role_derived_permissions(client):
    """The client branches on a capability rather than on a role name."""
    user = await _register(client)

    assert user["permissions"] == USER_PERMISSIONS


async def test_register_preserves_the_username_casing(client):
    """The unique index compares verbatim, so folding it here would break sign-in."""
    user = await _register(client, {**ADA, "username": "AdaLovelace"})

    assert user["username"] == "AdaLovelace"


async def test_register_treats_two_usernames_differing_only_in_case_as_distinct(client):
    await _register(client, {**ADA, "username": "AdaLovelace"})

    response = await client.post(
        "/api/v1/auth/register", json={**ADA, "username": "adalovelace", "email": "b@nexus.dev"}
    )

    assert response.status_code == 201, response.text


async def test_register_normalises_the_email(client):
    response = await client.post("/api/v1/auth/register", json={**ADA, "email": "  ADA@Nexus.DEV "})

    assert response.status_code == 201, response.text
    assert response.json()["email"] == "ada@nexus.dev"


async def test_register_rejects_a_duplicate_email(client, assert_error_envelope):
    """A taken address is a 409, and the message names neither field.

    The message used to be "An account with this email already exists.", which
    made this route an enumeration oracle with a field attached: a caller could
    walk a list of addresses through it and learn which were registered. The
    sentence below is shared with the username case, so the two are the same
    answer — see ``_IDENTIFIER_TAKEN``.
    """
    await _register(client)

    response = await client.post("/api/v1/auth/register", json={**ADA, "username": "someone-else"})

    error = assert_error_envelope(response, status_code=409, code="conflict")
    assert error["message"] == _TAKEN_MESSAGE
    assert ADA["password"] not in error["message"]


async def test_register_rejects_a_duplicate_username(client, assert_error_envelope):
    """A taken handle answers exactly what a taken address answers.

    Pinned as an equality with the email case rather than as a substring: the two
    were once two different sentences, and the difference between them was the
    whole of what this pair of tests used to protect.
    """
    await _register(client)

    response = await client.post("/api/v1/auth/register", json={**ADA, "email": "other@nexus.dev"})

    error = assert_error_envelope(response, status_code=409, code="conflict")
    assert error["message"] == _TAKEN_MESSAGE


async def test_register_refuses_a_field_it_does_not_own(client, assert_error_envelope):
    """``full_name`` is a 422 naming the field, not a 201 that drops it.

    Pydantic's default is ``extra="ignore"``, under which this request created an
    account with no name on it and answered 201: the caller read a successful
    signup and believed the name it sent had been recorded. The name field on this
    API is ``display_name``.
    """
    response = await client.post(
        "/api/v1/auth/register", json={**ADA, "full_name": "Ada Lovelace"}
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert any(
        entry["field"] == "full_name" for entry in error["details"]["errors"]
    ), error


@pytest.mark.parametrize(
    "password",
    ["short", "alllowercase-1", "ALLUPPERCASE-1", "NoDigitsHere", "NoSpecialsHere1"],
)
async def test_register_rejects_a_weak_password(client, assert_error_envelope, password):
    response = await client.post("/api/v1/auth/register", json={**ADA, "password": password})

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert any(entry["field"] == "password" for entry in error["details"]["errors"])


# -- Login and the session it opens -----------------------------------------


async def test_login_returns_a_token_pair_naming_its_session(client):
    await _register(client)

    body = await _login(client)

    assert set(body) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_in",
        "session_id",
    }
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"] != body["refresh_token"]
    assert uuid.UUID(body["session_id"])


async def test_login_persists_only_the_token_digest(client, db_session, settings):
    """A database dump must not yield a usable refresh token.

    This is the specific assertion that the raw token is never written: the
    column holds ``hash_token(refresh_token)``, and the raw value appears
    nowhere in the row.
    """
    await _register(client)
    body = await _login(client)
    user_id = uuid.UUID(_claims(body["access_token"], settings).get("sub"))

    rows = await _sessions_for(db_session, user_id)

    assert len(rows) == 1
    row = rows[0]
    assert row.id == uuid.UUID(body["session_id"])
    assert row.token_hash == hash_token(body["refresh_token"])
    assert row.token_hash != body["refresh_token"]
    assert len(row.token_hash) == 64
    assert row.revoked_at is None
    assert body["refresh_token"] not in row.token_hash


async def test_both_tokens_name_the_same_session_and_different_token_ids(client, settings):
    """The two tokens of a pair are distinguishable but share a device.

    Same ``sid`` so the device survives rotation; different ``jti`` so that
    revoking one does not silently revoke the other.
    """
    await _register(client)
    body = await _login(client)

    access = _claims(body["access_token"], settings)
    refresh = _claims(body["refresh_token"], settings)

    assert access["sid"] == refresh["sid"] == body["session_id"]
    assert access["jti"] != refresh["jti"]


async def test_each_sign_in_opens_a_separate_session(client, db_session, settings):
    """One row is one device sign-in, not one account."""
    await _register(client)
    first = await _login(client)
    second = await _login(client)

    assert first["session_id"] != second["session_id"]
    user_id = uuid.UUID(_claims(first["access_token"], settings)["sub"])
    assert len(await _sessions_for(db_session, user_id)) == 2


# -- /auth/me ----------------------------------------------------------------


async def test_me_returns_the_authenticated_user(client):
    user = await _register(client)
    tokens = await _login(client)

    response = await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))

    assert response.status_code == 200
    assert response.json()["id"] == user["id"]
    assert response.json()["email"] == ADA["email"]
    assert response.json()["permissions"] == USER_PERMISSIONS
    assert "hashed_password" not in response.text


async def test_me_without_a_bearer_token_is_an_enveloped_401(client, assert_error_envelope):
    response = await client.get("/api/v1/auth/me")

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["request_id"]
    assert response.headers["X-Request-ID"] == error["request_id"]


# -- Logout ------------------------------------------------------------------


async def test_a_logout_naming_another_token_leaves_the_callers_session_alone(
    client, assert_error_envelope
):
    """A body token that names a different session wins, and the bearer survives.

    Revoking an unusable body token used to fall through to the bearer branch as
    well, so a client retrying a logout with a refresh token it had already spent
    silently ended its own live session and was answered 204. A second session is
    asserted live afterwards so the refusal is not just "the first one died".
    """
    await _register(client)
    mine = await _login(client)
    theirs = await _login(client)

    response = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": "not-a-token"},
        headers=_bearer(mine["access_token"]),
    )
    assert response.status_code == 204, response.text

    survived = await client.get("/api/v1/auth/me", headers=_bearer(mine["access_token"]))
    assert survived.status_code == 200, survived.text
    untouched = await client.get("/api/v1/auth/me", headers=_bearer(theirs["access_token"]))
    assert untouched.status_code == 200, untouched.text


async def test_revoking_the_callers_own_session_says_so(client, assert_error_envelope):
    """The self-revoke carries a ``Warning``; every other revoke does not.

    Ending the session you are calling from is allowed — it is what the Sessions
    screen's "this is the device you are using right now" button does — but a bare
    204 is indistinguishable from revoking somebody's phone, and the very next
    request with that token is a 401. A header closes the gap without changing
    the status code a client legitimately signing itself out already handles.
    """
    await _register(client)
    mine = await _login(client)
    other = await _login(client)
    sessions = await client.get("/api/v1/auth/sessions", headers=_bearer(mine["access_token"]))
    assert sessions.status_code == 200, sessions.text
    other_id = next(
        row["id"]
        for row in sessions.json()["sessions"]
        if not row["is_current"] and row["id"] != other["session_id"]
    )

    someone_else = await client.delete(
        f"/api/v1/auth/sessions/{other_id}", headers=_bearer(mine["access_token"])
    )
    assert someone_else.status_code == 204, someone_else.text
    assert "Warning" not in someone_else.headers

    mine_id = sessions.json()["current_id"]
    revoked = await client.delete(
        f"/api/v1/auth/sessions/{mine_id}", headers=_bearer(mine["access_token"])
    )
    assert revoked.status_code == 204, revoked.text
    assert "revoked the session it was made with" in revoked.headers.get("Warning", "")


# -- Password change ---------------------------------------------------------


async def test_changing_the_password_requires_the_current_one(client, assert_error_envelope):
    await _register(client)
    tokens = await _login(client)

    response = await client.patch(
        "/api/v1/auth/password",
        json={"current_password": "not-the-password", "new_password": NEW_PASSWORD},
        headers=_bearer(tokens["access_token"]),
    )

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert "current password" in error["message"]


async def test_a_password_change_is_refused_without_a_bearer(client, assert_error_envelope):
    await _register(client)

    response = await client.patch(
        "/api/v1/auth/password",
        json={"current_password": ADA["password"], "new_password": NEW_PASSWORD},
    )

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_a_password_change_revokes_every_other_session_and_keeps_the_caller(
    client, assert_error_envelope
):
    """Only the caller's own device survives a password change.

    A password change is only half a control without the second half: every
    device still signed in with the old credential has to be closed too.
    """
    await _register(client)
    keep = await _login(client)
    other = await _login(client)

    response = await client.patch(
        "/api/v1/auth/password",
        json={"current_password": ADA["password"], "new_password": NEW_PASSWORD},
        headers=_bearer(keep["access_token"]),
    )

    assert response.status_code == 204
    assert response.content == b""
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(keep["access_token"]))
    ).status_code == 200
    assert_error_envelope(
        await client.get("/api/v1/auth/me", headers=_bearer(other["access_token"])),
        status_code=401,
        code="unauthorized",
    )


async def test_the_new_password_works_and_the_old_one_does_not(client, assert_error_envelope):
    await _register(client)
    tokens = await _login(client)
    await client.patch(
        "/api/v1/auth/password",
        json={"current_password": ADA["password"], "new_password": NEW_PASSWORD},
        headers=_bearer(tokens["access_token"]),
    )

    assert (
        await client.post("/api/v1/auth/login", json={**ADA, "password": NEW_PASSWORD})
    ).status_code == 200
    assert_error_envelope(
        await client.post("/api/v1/auth/login", json=ADA),
        status_code=401,
        code="unauthorized",
    )


async def test_a_password_change_may_not_reuse_the_current_password(client, assert_error_envelope):
    await _register(client)
    tokens = await _login(client)

    response = await client.patch(
        "/api/v1/auth/password",
        json={"current_password": ADA["password"], "new_password": ADA["password"]},
        headers=_bearer(tokens["access_token"]),
    )

    assert_error_envelope(response, status_code=409, code="conflict")


async def test_a_password_change_rejects_a_weak_new_password(client, assert_error_envelope):
    await _register(client)
    tokens = await _login(client)

    response = await client.patch(
        "/api/v1/auth/password",
        json={"current_password": ADA["password"], "new_password": "weak"},
        headers=_bearer(tokens["access_token"]),
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert any(entry["field"] == "new_password" for entry in error["details"]["errors"])


async def test_a_password_change_spends_outstanding_reset_tokens(
    client, db_session, assert_error_envelope
):
    """A reset link issued before the change must not set a password afterwards."""
    await _register(client)
    tokens = await _login(client)
    requested = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    stale_token = requested.json()["dev_token"]

    await client.patch(
        "/api/v1/auth/password",
        json={"current_password": ADA["password"], "new_password": NEW_PASSWORD},
        headers=_bearer(tokens["access_token"]),
    )

    result = await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(stale_token))
    )
    assert result.scalar_one().used_at is not None

    assert_error_envelope(
        await client.post(
            "/api/v1/auth/password/reset",
            json={"token": stale_token, "new_password": "Another-Rotated-Pass-3"},
        ),
        status_code=401,
        code="unauthorized",
    )


# -- Password reset ----------------------------------------------------------


async def test_the_token_is_withheld_unless_the_install_asks_for_it(client, monkeypatch):
    """The raw token is opt-in, and the opt-in is what this module turns on.

    Without it, ``/auth/password/forgot`` answers ``{"dev_token": null}`` for a
    known address — the same body it gives an unknown one. That is the whole
    point of the flag: the endpoint may not become an account oracle, and a
    token in the response would be an unauthenticated takeover for anyone who
    can guess an address. A local install with no mail transport turns the flag
    on; nothing else should.
    """
    from app.core.config import get_settings

    monkeypatch.setenv("DEV_EXPOSE_RESET_TOKEN", "false")
    get_settings.cache_clear()
    await _register(client)

    known = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    unknown = await client.post("/api/v1/auth/password/forgot", json={"email": "nobody@nexus.dev"})

    assert known.status_code == unknown.status_code == 202
    assert known.json()["dev_token"] is None
    # And now indistinguishable in the body itself, which is the property the
    # default exists to preserve.
    assert known.json() == unknown.json()


async def test_a_reset_request_answers_identically_for_known_and_unknown_addresses(client):
    """The endpoint must not be an account oracle.

    Same status, same keys, same ``accepted`` flag. Outside production the
    ``dev_token`` differs by design — that is what makes a local install
    completable at all — so the identical-shape claim is the one that holds in
    every environment, and it is the shape a client is told to branch on.
    """
    await _register(client)

    known = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    unknown = await client.post("/api/v1/auth/password/forgot", json={"email": "nobody@nexus.dev"})

    assert known.status_code == unknown.status_code == 202
    assert set(known.json()) == set(unknown.json()) == {"accepted", "dev_token"}
    assert known.json()["accepted"] is unknown.json()["accepted"] is True
    assert unknown.json()["dev_token"] is None
    assert known.json()["dev_token"]


async def test_a_reset_token_sets_a_new_password_and_ends_every_session(
    client, assert_error_envelope
):
    await _register(client)
    tokens = await _login(client)
    requested = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})

    response = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": requested.json()["dev_token"], "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 204, response.text
    assert (
        await client.post("/api/v1/auth/login", json={**ADA, "password": NEW_PASSWORD})
    ).status_code == 200
    assert_error_envelope(
        await client.post("/api/v1/auth/login", json=ADA),
        status_code=401,
        code="unauthorized",
    )
    # This is the recovery path for a compromised account: leaving a live
    # session behind would leave the compromise in place behind a new password.
    assert_error_envelope(
        await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"])),
        status_code=401,
        code="unauthorized",
    )


async def test_a_reset_token_cannot_be_redeemed_twice(client, assert_error_envelope):
    await _register(client)
    requested = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    token = requested.json()["dev_token"]

    first = await client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert first.status_code == 204

    second = await client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": "Yet-Another-Pass-5"}
    )

    assert_error_envelope(second, status_code=401, code="unauthorized")


async def test_a_reset_token_is_stored_only_as_its_digest(client, db_session):
    """Same rule as a session: a dump must not yield a usable reset token."""
    await _register(client)
    requested = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    raw_token = requested.json()["dev_token"]

    result = await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw_token))
    )
    row = result.scalar_one()
    assert row.token_hash != raw_token
    assert row.used_at is None


@pytest.mark.parametrize(
    "token",
    [
        "a" * 40,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJub2tlIn0.signature",
    ],
    ids=["opaque", "forged-signature"],
)
async def test_a_token_that_was_never_issued_is_rejected(client, assert_error_envelope, token):
    """A token that never existed is refused, whatever shape it arrives in.

    ``"a" * 40`` is not a JWT at all and the forged signature is a well-formed
    one that this deployment did not sign; neither survives ``decode_token``, and
    both answer 401 rather than a 500. The invariant is deliberately *not* that
    every possible string is accepted here — ``PasswordResetConfirm.token``
    carries ``min_length=20``, so a shorter value is a malformed request and is
    covered separately by
    :func:`test_a_reset_token_shorter_than_a_token_is_a_validation_error`. That
    is a different failure from an authentication one and it must stay one:
    folding it in here would have made the parameterisation claim a 401 for a
    body the router never let reach the service.

    A cryptographically *genuine* token that resolves to nothing is the case that
    is easy to get wrong, because it gets furthest into the handler:
    :func:`test_an_unknown_expired_and_spent_token_answer_identically` covers it.
    """
    await _register(client)

    response = await client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD}
    )

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_a_reset_token_shorter_than_a_token_is_a_validation_error(
    client, assert_error_envelope
):
    """A truncated value is a bad request, not a failed authentication.

    The floor exists so a client that posts half a link is told so, instead of
    being handed the same "invalid or has expired" it would get for a link that
    really did expire. Asserted as its own case for that reason.
    """
    await _register(client)

    response = await client.post(
        "/api/v1/auth/password/reset", json={"token": "not.a.jwt", "new_password": NEW_PASSWORD}
    )

    assert_error_envelope(response, status_code=422, code="validation_error")


async def test_an_unknown_expired_and_spent_token_answer_identically(
    client, db_session, assert_error_envelope
):
    """The security-critical assertion in this section.

    A reset link is a bearer credential, so an attacker holding one must not be
    able to learn anything from the refusal: not whether a token was ever issued
    for that account, not whether it has already been redeemed, and not whether
    it simply timed out. All three resolve to the same lookup finding no usable
    row, and this holds them to the *same message* — comparing only the status
    code or the error code would let the wording drift into an oracle.

    The unknown token is a real one, signed by this deployment and then absent
    from the table, because that is the case that travels furthest through
    ``complete_password_reset`` before being refused: it clears the signature,
    the token type, the purpose claim and the ``jti``, and fails only at the
    lookup. The forged and opaque shapes are refused much earlier, so they prove
    nothing about this.
    """
    await _register(client)

    def _redeem(token: str, new_password: str = NEW_PASSWORD):
        return client.post(
            "/api/v1/auth/password/reset", json={"token": token, "new_password": new_password}
        )

    unknown_row = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    unknown_token = unknown_row.json()["dev_token"]
    await db_session.execute(
        delete(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(unknown_token))
    )
    await db_session.commit()

    spent_row = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    spent_token = spent_row.json()["dev_token"]
    assert (await _redeem(spent_token)).status_code == 204

    expired_row = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    expired_token = expired_row.json()["dev_token"]
    await db_session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.token_hash == hash_token(expired_token))
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db_session.commit()

    unknown = await _redeem(unknown_token, NEVER_APPLIED_ONE)
    spent = await _redeem(spent_token, NEVER_APPLIED_TWO)
    expired = await _redeem(expired_token, NEVER_APPLIED_TWO)

    assert (unknown.status_code, spent.status_code, expired.status_code) == (401, 401, 401)
    errors = [response.json()["error"] for response in (unknown, spent, expired)]
    for error in errors:
        assert error["code"] == "unauthorized"
        assert error["details"] is None
    assert errors[0]["message"] == errors[1]["message"] == errors[2]["message"]

    # The three refusals were inert. The only redemption in this test that was
    # allowed to take effect is the one that built the spent state, so the
    # password is the one it set — and neither password the refused attempts
    # carried ever reached the column.
    assert (
        await client.post("/api/v1/auth/login", json={**ADA, "password": NEW_PASSWORD})
    ).status_code == 200
    for never_applied in (NEVER_APPLIED_ONE, NEVER_APPLIED_TWO):
        assert_error_envelope(
            await client.post("/api/v1/auth/login", json={**ADA, "password": never_applied}),
            status_code=401,
            code="unauthorized",
        )


async def test_a_reset_request_rejects_a_malformed_address(client, assert_error_envelope):
    response = await client.post("/api/v1/auth/password/forgot", json={"email": "not-an-address"})

    assert_error_envelope(response, status_code=422, code="validation_error")


async def test_a_reset_token_cannot_be_used_to_rotate_a_session(client, assert_error_envelope):
    """A reset link cannot be exchanged for a session.

    The ``purpose`` claim is what stops it being replayed as a refresh token,
    which would otherwise mint a full access/refresh pair.
    """
    await _register(client)
    requested = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": requested.json()["dev_token"]}
    )

    assert_error_envelope(response, status_code=401, code="unauthorized")


# -- Audit trail -------------------------------------------------------------


async def test_a_sign_in_writes_a_user_login_row(client, db_session, settings):
    await _register(client)
    body = await _login(client)
    user_id = uuid.UUID(_claims(body["access_token"], settings)["sub"])

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.user_id == user_id,
            AuditLog.event_type == AuditEvent.USER_LOGIN.value,
        )
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].metadata_ == {}
    assert rows[0].created_at is not None


async def test_registration_writes_a_user_registered_row(client, db_session):
    user = await _register(client)

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.user_id == uuid.UUID(user["id"]),
            AuditLog.event_type == AuditEvent.USER_REGISTERED.value,
        )
    )
    row = result.scalar_one()
    # The username is what this event is about; the address deliberately is not,
    # because every later event points at ``user_id`` instead.
    assert row.metadata_ == {"username": ADA["username"]}


async def test_a_failed_sign_in_for_an_unknown_address_writes_a_null_user_row(client, db_session):
    """The most valuable event in the trail, and the reason ``user_id`` is nullable."""
    response = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@nexus.dev", "password": "Whatever-Pass-1"}
    )
    assert response.status_code == 401

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.event_type == AuditEvent.USER_LOGIN_FAILED.value)
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].user_id is None


async def test_a_failed_sign_in_for_a_known_address_is_attributed_to_that_account(
    client, db_session
):
    user = await _register(client)

    response = await client.post("/api/v1/auth/login", json={**ADA, "password": "wrong-Pass-9"})
    assert response.status_code == 401

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.user_id == uuid.UUID(user["id"]),
            AuditLog.event_type == AuditEvent.USER_LOGIN_FAILED.value,
        )
    )
    assert len(list(result.scalars().all())) == 1


async def test_a_password_change_writes_a_password_changed_row(client, db_session):
    await _register(client)
    tokens = await _login(client)

    await client.patch(
        "/api/v1/auth/password",
        json={"current_password": ADA["password"], "new_password": NEW_PASSWORD},
        headers=_bearer(tokens["access_token"]),
    )

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.event_type == AuditEvent.PASSWORD_CHANGED.value)
    )
    row = result.scalar_one()
    assert row.metadata_["revoked_sessions"] == 0


async def test_the_sign_in_stamps_the_last_login_timestamp(client, db_session, settings):
    """The account reflects that a sign-in happened, not just that it succeeded."""
    await _register(client)
    body = await _login(client)
    user_id = uuid.UUID(_claims(body["access_token"], settings)["sub"])

    result = await db_session.execute(select(User).where(User.id == user_id))
    assert result.scalar_one().last_login_at is not None
