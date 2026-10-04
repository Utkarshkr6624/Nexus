"""Regression tests for the security fixes, with the evidence each one rests on.

Ten defects were reproduced by an audit and fixed; this file locks down the six
that are about credentials and the audit trail, plus the shared doubles the
sibling :mod:`tests.test_regressions_users` imports. Each block below is one
bug, and the shape of the evidence follows from what each guarantee is *made
of*:

* Where the guarantee lives in the SQL text — the four predicates of the
  conditional rotation, ``used_at IS NULL`` on the reset claim, the expiry
  filter on the session listing — the statement the *repository itself builds*
  is captured from an in-memory ``AsyncSession`` and compiled with the
  postgresql dialect. That is the real statement, not a restatement of it, and
  it needs no server.
* Where the guarantee lives in the service — refusing a lost rotation with the
  same message as every other refusal, clamping a rotated expiry, falling back
  to the signed ``sid`` on a bearer logout — the real service is driven against
  in-memory doubles, with the real repository underneath where the repository's
  own answer is what the service is reacting to.
* Where the guarantee is an HTTP status, the real application is driven with
  its dependencies overridden. ``/auth/me`` is reachable that way with no
  database at all, so those assertions are genuinely executed rather than
  declared.
* What only means anything over the wire — the end-to-end replay, the
  end-to-end double redemption, the real revocation, the listing of a lapsed
  row — is marked ``integration`` and runs against the ``nexus_test`` database
  the suite's conftest provisions.

**Every one of these is executed.** The session-listing defect once reported
here — ``app/api/v1/auth.py`` building a ``SessionRead`` with a Pydantic
keyword that does not exist, so ``GET /api/v1/auth/sessions`` answered 500 —
has been fixed in the application; ``SessionRead.for_request`` is what the
router calls now, and ``test_an_expired_session_is_not_listed`` passes against
it rather than standing as a claim.
"""

from __future__ import annotations

import contextlib
import inspect
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Delete, Update
from sqlalchemy.dialects import postgresql

from app.api.deps import _SESSION_NOT_LIVE, get_session_repository
from app.core.config import Settings, get_settings
from app.core.deps import get_user_repository
from app.core.exceptions import UnauthorizedError
from app.core.security import create_access_token, create_refresh_token, hash_token
from app.db.session import get_db
from app.main import create_app
from app.models.audit import AuditEvent, AuditLog
from app.models.password_reset import PasswordResetToken
from app.models.session import Session
from app.models.user import User
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.session import SessionRepository
from app.services.audit_service import AuditService
from app.services.auth_service import _INVALID_RESET, AuthService
from app.services.session_service import _INVALID_SESSION, SessionService

STRONG_SECRET = "k7Qm2Zt9XpL4vR8sN6bY3wJ1hG5dF0cA7eU9iO2pS4tV6xZ8mB1nC3qW5eR7yT9u"

#: Stands in for the database's ``now()`` in a compiled statement.
DATABASE_NOW = "now()"

#: Named rather than inlined so the compiled-SQL assertions read as predicates
#: rather than as credential-looking literals.
PRESENTED_DIGEST = "presented-digest"
REPLACEMENT_DIGEST = "replacement-digest"
UNRELATED_DIGEST = "an-unrelated-digest"

#: Two distinct replacement passwords, so "the replay never wrote one of its
#: own" is observable rather than assumed.
FIRST_PASSWORD = "First-Take-9"
SECOND_PASSWORD = "Second-Take-9"
ACCOUNT_PASSWORD = "Correct-Horse-7"

#: The three messages compared below are read from their own modules rather than
#: copied in here, so the assertions are about the messages *agreeing with each
#: other* rather than about a literal a future edit could move in one place and
#: leave the other behind.


# ---------------------------------------------------------------------------
# Shared doubles
# ---------------------------------------------------------------------------


def compiled_sql(statement) -> str:
    """Render a statement the way PostgreSQL would receive it.

    The postgresql dialect is named explicitly rather than left to the default
    so the text under assertion is the text the deployed database is asked to
    run — ``now()`` in particular renders as ``CURRENT_TIMESTAMP`` elsewhere.
    """
    return str(statement.compile(dialect=postgresql.dialect()))


#: The table a read names, so one staged session can answer for several tables.
_TABLE_IN_FROM = re.compile(r"FROM\s+(\w+)")


class _FakeResult:
    """The three shapes of ``AsyncSession.execute``'s return the code under test uses."""

    def __init__(self, *, rowcount: int = 0, scalar: object = None, rows: list | None = None):
        self.rowcount = rowcount
        self._scalar = scalar
        self._rows = list(rows or [])

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def scalar_one(self) -> object:
        return self._scalar

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list:
        return list(self._rows)


class FakeAsyncSession:
    """An in-memory ``AsyncSession`` that records the statements it is handed.

    This is the whole point of the SQL assertions in this file: the statements
    captured here are the ones the repositories *built*, so a predicate dropped
    from a ``WHERE`` clause shows up as a missing string rather than passing
    because a test wrote its own copy of the query.

    Reads answer with whatever the test staged — ``selectors`` keyed by the table
    a read names, so one session can hold a session row and a user row at once,
    and ``count`` for a ``count(*)``. Writes answer with ``rowcount``, which is
    exactly the signal the conditional-update fixes turn on: a zero-row case is
    reproducible without a database, against the real repository.
    """

    def __init__(self) -> None:
        self.statements: list = []
        self.added: list = []
        self.commits = 0
        self.refreshed: list = []
        self.rollbacks = 0
        #: Rows a write statement claims to have matched.
        self.rowcount = 1
        #: What a single-row read returns, per table named in the statement.
        self.selectors: dict[str, object] = {}
        #: Fallback for a read whose table was not staged.
        self.scalar: object = None
        #: What a listing read returns.
        self.rows: list = []
        #: What a ``count(*)`` read returns.
        self.count = 0

    async def execute(self, statement):
        """Record the statement and answer from whatever the test staged."""
        self.statements.append(statement)
        if isinstance(statement, (Update, Delete)):
            return _FakeResult(rowcount=self.rowcount)
        text = compiled_sql(statement)
        if "count(" in text:
            return _FakeResult(scalar=self.count)
        table = _TABLE_IN_FROM.search(text)
        key = table.group(1) if table else ""
        return _FakeResult(scalar=self.selectors.get(key, self.scalar), rows=self.rows)

    def add(self, instance: object) -> None:
        """Remember an instance the caller asked to persist."""
        self.added.append(instance)

    async def commit(self) -> None:
        """Count the commit; there is nothing to flush."""
        self.commits += 1

    async def refresh(self, instance: object) -> None:
        """Remember the re-read, so a test can assert one did not happen."""
        self.refreshed.append(instance)

    async def rollback(self) -> None:
        """Count the rollback: the signal item 9 is about."""
        self.rollbacks += 1

    async def close(self) -> None:
        """Close is a no-op; the test owns the session's whole lifetime."""
        return None

    async def delete(self, instance: object) -> None:
        """Forget the instance, mirroring what a real delete would do to the session."""
        self.added.remove(instance)

    @property
    def last_sql(self) -> str:
        """The statement most recently handed to ``execute``, as PostgreSQL text."""
        return compiled_sql(self.statements[-1])


class _StubSessionRepository:
    """A session repository that records what the service asked it to do.

    Only the surface :class:`SessionService` actually calls is implemented. It
    exists for the two questions a double can answer that a real repository
    cannot here: *which* lookup the service settled on, and *which* row it acted
    on. "Logout ends the session" is the claim that the row ends up revoked
    rather than merely that a call happened, and the ownership question is the
    argument list of ``get_by_id_for_user``.

    It is deliberately *not* used by the rotation-refusal tests: those run the
    real :class:`SessionRepository` over a staged session, because there the
    repository's own answer is what the service is reacting to.
    """

    def __init__(self, *, row: Session | None, session: FakeAsyncSession | None = None):
        self.session = session or FakeAsyncSession()
        self.row = row
        self.rotation_calls: list[dict] = []
        self.revoke_calls: list[Session] = []
        self.ownership_lookups: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.touched: list[datetime] = []

    async def get_by_id(self, session_id: uuid.UUID) -> Session | None:
        return self.row

    async def get_by_id_for_user(self, session_id: uuid.UUID, user_id: uuid.UUID) -> Session | None:
        self.ownership_lookups.append((session_id, user_id))
        if self.row is None or self.row.user_id != user_id:
            return None
        return self.row

    async def rotate_token_if_current(self, db_session: Session, **fields) -> bool:
        """Record the write the service is about to make, and report it landed.

        Always succeeds: the clamp is what the caller of this method under test
        is examining, and a lost rotation is a different question with its own
        test.
        """
        self.rotation_calls.append(fields)
        return True

    async def touch(self, db_session: Session, *, last_used_at: datetime) -> Session:
        self.touched.append(last_used_at)
        return db_session

    async def revoke(self, db_session: Session, *, revoked_at: datetime) -> Session:
        self.revoke_calls.append(db_session)
        db_session.revoked_at = revoked_at
        return db_session

    async def list_for_user(self, user_id: uuid.UUID, *, include_inactive: bool = False) -> list:
        return []


class _StubUserRepository:
    """A user repository that answers one id lookup and records what was written."""

    def __init__(self, user: User, session: FakeAsyncSession | None = None):
        self.session = session or FakeAsyncSession()
        self._user = user
        self.written: list[dict] = []
        self.username_checks: list[tuple[str, uuid.UUID | None]] = []
        self.list_all_calls = 0

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self._user if self._user.id == user_id else None

    async def get_by_email(self, email: str) -> User | None:
        return self._user

    async def update_fields(self, user: User, **fields) -> User:
        self.written.append(fields)
        for key, value in fields.items():
            setattr(user, key, value)
        return user

    async def exists_by_email(self, email: str) -> bool:
        return False

    async def exists_by_username(
        self, username: str, *, exclude_user_id: uuid.UUID | None = None
    ) -> bool:
        self.username_checks.append((username, exclude_user_id))
        return False

    async def list_all(self) -> list[User]:
        self.list_all_calls += 1
        return [self._user]

    async def delete(self, user: User) -> None:
        return None


class _RecordingAudit:
    """An audit sink that keeps what it was asked to record."""

    def __init__(self) -> None:
        self.events: list[tuple[object, uuid.UUID | None, dict | None]] = []

    async def record(self, event, *, user_id=None, ip_address=None, user_agent=None, metadata=None):
        self.events.append((event, user_id, metadata))
        return None


class _FailingAuditRepository:
    """An audit repository whose write always fails, with a recorded session."""

    def __init__(self, session: FakeAsyncSession, error: Exception | None = None):
        self.session = session
        self._error = error or RuntimeError("audit table unavailable")

    async def record(self, **fields) -> AuditLog:
        raise self._error


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        secret_key=STRONG_SECRET,
        **{"session_absolute_lifetime_days": 30, "refresh_token_expire_days": 7, **overrides},
    )


def _user(*, role: str = "user", active: bool = True) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email="ada@nexus.dev",
        username="ada",
        hashed_password="x",  # noqa: S106
        display_name="Ada Lovelace",
        role=role,
        is_active=active,
        is_verified=True,
        created_at=now,
        updated_at=now,
    )


def _session_row(
    *,
    user_id: uuid.UUID,
    token_hash: str,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> Session:
    created = created_at or datetime.now(UTC)
    return Session(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=token_hash,
        user_agent="pytest",
        ip_address="127.0.0.1",
        created_at=created,
        updated_at=created,
        expires_at=expires_at or created + timedelta(days=30),
        revoked_at=revoked_at,
    )


# ---------------------------------------------------------------------------
# 1. Refresh rotation: the conditional update, and the message it is refused with
# ---------------------------------------------------------------------------


def test_rotate_token_if_current_is_a_boolean_conditional_update():
    """The method exists, and its shape is the shape of the guarantee.

    ``bool`` in, ``bool`` out, and every value a caller supplies keyword-only:
    a rotation that could be called without saying which digest it is claiming
    is a rotation that can be called without meaning anything.
    """
    method = SessionRepository.rotate_token_if_current

    assert inspect.iscoroutinefunction(method)
    parameters = inspect.signature(method).parameters
    assert list(parameters) == [
        "self",
        "db_session",
        "current_token_hash",
        "token_hash",
        "expires_at",
    ]
    for name in ("current_token_hash", "token_hash", "expires_at"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(method).return_annotation in (bool, "bool")


async def test_the_rotation_statement_carries_all_four_predicates():
    """The condition is in the ``WHERE`` clause, not in a read that precedes it.

    A read-then-write is check-then-act: under ``READ COMMITTED`` the second
    request still sees the digest the first has not yet committed overwriting,
    so both pass the check and both write. Folding the expectation into the
    statement is what makes a double-spend a conflict the database detects, and
    it is only true if all four conditions are on the one statement.
    """
    session = FakeAsyncSession()
    repository = SessionRepository(session)
    row = _session_row(user_id=uuid.uuid4(), token_hash=hash_token("presented"))

    assert await repository.rotate_token_if_current(
        row,
        current_token_hash=PRESENTED_DIGEST,
        token_hash=REPLACEMENT_DIGEST,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    sql = session.last_sql
    assert sql.startswith("UPDATE sessions")
    assert "sessions.id =" in sql
    assert "sessions.token_hash =" in sql
    assert "sessions.revoked_at IS NULL" in sql
    assert f"sessions.expires_at > {DATABASE_NOW}" in sql
    # The new digest is written; the old one is only compared against.
    assert "SET token_hash=" in sql


async def test_a_rotation_that_matches_nothing_is_reported_as_lost():
    """``False`` is the answer, not an exception and not a silent success.

    ``bool(rowcount)`` is the entire signal: a replayed token, a concurrent
    rotation and a revocation that landed first all reduce to "this statement
    matched no row", and the caller is not given the means to tell them apart.
    """
    session = FakeAsyncSession()
    session.rowcount = 0
    repository = SessionRepository(session)
    row = _session_row(user_id=uuid.uuid4(), token_hash=hash_token("presented"))

    assert (
        await repository.rotate_token_if_current(
            row,
            current_token_hash=PRESENTED_DIGEST,
            token_hash=REPLACEMENT_DIGEST,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        is False
    )
    # Nothing was rotated, so nothing is re-read to hand back.
    assert session.refreshed == []
    # The commit still happened: the statement ran, it simply matched nothing.
    assert session.commits == 1


async def test_a_lost_rotation_is_refused_with_the_shared_session_message():
    """The central assertion of this block.

    A caller that loses the conditional update has to be told exactly what a
    caller with an unknown session, a revoked session, an expired session or a
    rotated-away token is told. Anything else turns the difference between "this
    token is spent" and "this token never existed" into an oracle.

    Driven through the **real** :class:`SessionRepository` with a session that
    reports a zero rowcount, so the ``False`` that has to be refused is the one
    the database would really have produced — not a value a double invented.
    """
    user = _user()
    row = _session_row(
        user_id=user.id,
        token_hash=hash_token("refresh-token"),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    refresh = create_refresh_token(
        user.id, settings=_settings(), extra_claims={"sid": str(row.id), "jti": "jti-1"}
    )
    session = FakeAsyncSession()
    session.selectors = {"sessions": row, "users": user}
    session.rowcount = 0
    service = SessionService(SessionRepository(session), _settings())

    with pytest.raises(UnauthorizedError) as lost:
        await service.rotate(refresh_token=refresh)

    assert str(lost.value) == _INVALID_SESSION


async def test_every_rotation_rejection_carries_the_same_message():
    """Five ways to fail, one string.

    The pre-fix defect was a rejection that answered differently, which is what
    makes a replay attempt distinguishable from a guess. Each case below fails
    at a different point in :meth:`SessionService.rotate` and every one of them
    must land on the same message. The repository is the real one throughout; only
    the session underneath it is staged, so the last case really does lose a
    conditional ``UPDATE``.
    """
    settings = _settings()
    user = _user()
    now = datetime.now(UTC)
    refresh = create_refresh_token(
        user.id, settings=settings, extra_claims={"sid": str(uuid.uuid4()), "jti": "jti-2"}
    )

    def _service(row: Session | None, *, rotation_matched: bool = True):
        session = FakeAsyncSession()
        session.selectors = {"sessions": row, "users": user}
        session.rowcount = 1 if rotation_matched else 0
        return SessionService(SessionRepository(session), settings)

    # A sid that names no row at all.
    unknown = _service(None)
    with pytest.raises(UnauthorizedError) as exc_unknown:
        await unknown.rotate(refresh_token=refresh)

    # A row that was signed out, and one whose token has expired.
    revoked_row = _session_row(
        user_id=user.id, token_hash=hash_token(refresh), expires_at=now + timedelta(days=7)
    )
    revoked_row.revoked_at = now
    revoked = _service(revoked_row)
    with pytest.raises(UnauthorizedError) as exc_revoked:
        await revoked.rotate(refresh_token=refresh)

    expired = _service(
        _session_row(
            user_id=user.id, token_hash=hash_token(refresh), expires_at=now - timedelta(seconds=1)
        )
    )
    with pytest.raises(UnauthorizedError) as exc_expired:
        await expired.rotate(refresh_token=refresh)

    # A token that has already been rotated away: the row holds another digest.
    rotated_away = _service(
        _session_row(
            user_id=user.id,
            token_hash=hash_token(UNRELATED_DIGEST),
            expires_at=now + timedelta(days=7),
        )
    )
    with pytest.raises(UnauthorizedError) as exc_rotated:
        await rotated_away.rotate(refresh_token=refresh)

    # The rotation itself losing the race.
    lost = _service(
        _session_row(
            user_id=user.id, token_hash=hash_token(refresh), expires_at=now + timedelta(days=7)
        ),
        rotation_matched=False,
    )
    with pytest.raises(UnauthorizedError) as exc_lost:
        await lost.rotate(refresh_token=refresh)

    messages = {
        str(exc.value) for exc in (exc_unknown, exc_revoked, exc_expired, exc_rotated, exc_lost)
    }
    assert len(messages) == 1, messages
    assert messages.pop() == _INVALID_SESSION


def test_the_api_layer_and_the_service_agree_on_the_session_message():
    """The two modules that answer for a dead session must not drift apart.

    ``get_authenticated_user`` and ``SessionService.rotate`` are separate
    modules with separate constants. If they ever disagree, a caller learns
    something from *which* endpoint refused, which is the same oracle in a new
    place.
    """
    assert _SESSION_NOT_LIVE == _INVALID_SESSION


@pytest.mark.integration
async def test_a_replayed_refresh_token_is_refused_end_to_end(client):
    """Two clients presenting the same refresh token: one wins, one is refused.

    The refusal is the property; the unit test above proves the message, this
    one proves it over the wire. Runs against the real database.
    """
    from tests.test_sessions import ADA, _bearer, _sign_in

    await client.post("/api/v1/auth/register", json=ADA)
    tokens = await _sign_in(client, ADA)

    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 401, replay.text
    # The new pair is still the only live one: the replay must not have
    # invalidated the legitimate client's tokens by overwriting the digest.
    # 401 here is exactly the failure this test exists to catch — the replay
    # revoking the session the legitimate client is still using — so there is
    # nothing to allow here.
    me = await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    assert me.status_code == 200, me.text


# ---------------------------------------------------------------------------
# 2. Password reset: one redemption, claimed before the password is written
# ---------------------------------------------------------------------------


async def test_the_reset_claim_is_a_conditional_update_on_used_at():
    """``used_at IS NULL`` rides along on the write, not just on the read.

    A blind ``UPDATE ... WHERE id = ?`` overwrites ``used_at`` and reports
    success to every caller, which is the window in which a stolen reset link
    overwrites the password the legitimate owner just set. The condition has to
    be in the statement.
    """
    session = FakeAsyncSession()
    repository = PasswordResetRepository(session)

    assert await repository.spend(uuid.uuid4(), used_at=datetime.now(UTC)) is True

    sql = session.last_sql
    assert sql.startswith("UPDATE password_reset_tokens")
    assert "password_reset_tokens.id =" in sql
    assert "password_reset_tokens.used_at IS NULL" in sql
    assert "SET used_at=" in sql


async def test_a_reset_token_that_is_already_spent_is_reported_as_unclaimed():
    session = FakeAsyncSession()
    session.rowcount = 0
    repository = PasswordResetRepository(session)

    assert await repository.spend(uuid.uuid4(), used_at=datetime.now(UTC)) is False
    assert "used_at IS NULL" in session.last_sql


async def test_the_read_that_precedes_the_claim_already_filters_spent_and_expired():
    """Unknown, spent and expired all resolve to ``None`` at this one read.

    That is what lets the service answer all of them with one message: they are
    not three branches, they are one branch taken three ways.
    """
    session = FakeAsyncSession()
    repository = PasswordResetRepository(session)
    now = datetime.now(UTC)

    await repository.get_valid_by_token_hash("a-digest", now=now)

    sql = session.last_sql
    assert sql.startswith("SELECT")
    assert "password_reset_tokens.token_hash =" in sql
    assert "password_reset_tokens.used_at IS NULL" in sql
    assert "password_reset_tokens.expires_at >" in sql


def _reset_token(user: User, settings: Settings) -> str:
    return create_refresh_token(
        user.id,
        settings=settings,
        extra_claims={"jti": str(uuid.uuid4()), "purpose": "password_reset"},
    )


def _reset_row(user: User, token: str) -> PasswordResetToken:
    """An outstanding reset row for ``token``: unused and unexpired."""
    now = datetime.now(UTC)
    return PasswordResetToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=hash_token(token),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=30),
        used_at=None,
    )


def _reset_auth_service(
    *, user: User, row: PasswordResetToken | None, spend_rowcount: int, sessions=None
) -> tuple[AuthService, _StubUserRepository]:
    """An :class:`AuthService` whose reset repository is the real one.

    ``AuthService`` builds its ``PasswordResetRepository`` from the user
    repository's session, so staging the outcome on that session is enough to
    make the *real* ``get_valid_by_token_hash`` and ``spend`` run — the code the
    fix actually changed — without a database.
    """
    session = FakeAsyncSession()
    session.scalar = row
    session.rowcount = spend_rowcount
    repository = _StubUserRepository(user, session=session)
    return AuthService(repository, sessions, None, _settings()), repository


async def test_a_reset_link_cannot_be_redeemed_twice():
    """The second redemption is refused, and nothing is written for it.

    A redemption that reads the row and then writes the password is
    check-then-act in exactly the same way the rotation was. The claim has to
    come first, and losing it has to abort the flow before the new password is
    hashed, let alone stored.
    """
    user = _user()
    token = _reset_token(user, _settings())
    service, repository = _reset_auth_service(
        user=user, row=_reset_row(user, token), spend_rowcount=0
    )

    with pytest.raises(UnauthorizedError) as refused:
        await service.complete_password_reset(token=token, new_password=SECOND_PASSWORD)

    assert str(refused.value) == _INVALID_RESET
    assert repository.written == [], "a lost claim must not write a password"


async def test_the_first_redemption_of_a_link_succeeds_and_claims_it():
    """The guard is not "refuse everything": a rowcount of one goes through."""
    user = _user()
    token = _reset_token(user, _settings())
    service, repository = _reset_auth_service(
        user=user, row=_reset_row(user, token), spend_rowcount=1
    )

    await service.complete_password_reset(token=token, new_password=FIRST_PASSWORD)

    assert repository.written and "hashed_password" in repository.written[0]
    assert repository.written[0]["hashed_password"] != "First-Take-9"


async def test_every_reset_rejection_carries_the_same_message():
    """Unknown, expired, spent and wrong-kind all answer identically.

    "Expired" and "already spent" are decided in the read, so they arrive here
    as the same ``None`` as an unknown digest; the other two are decided in the
    service. One message covers all of them, which is the property.
    """
    settings = _settings()
    user = _user()
    token = _reset_token(user, settings)
    not_a_reset = create_refresh_token(
        user.id, settings=settings, extra_claims={"sid": str(uuid.uuid4()), "jti": "jti-3"}
    )
    access = create_access_token(user.id, settings=settings, extra_claims={"jti": "jti-4"})

    async def _message_for(*, row, spend_rowcount: int, candidate: str) -> str:
        service, _ = _reset_auth_service(user=user, row=row, spend_rowcount=spend_rowcount)
        with pytest.raises(UnauthorizedError) as refused:
            await service.complete_password_reset(token=candidate, new_password=SECOND_PASSWORD)
        return str(refused.value)

    # An unknown, already-spent or expired digest: the lookup resolves to
    # nothing at all, and the conditional claim is never reached.
    unknown = await _message_for(row=None, spend_rowcount=0, candidate=token)
    # A row that resolves but whose claim loses the race.
    lost = await _message_for(row=_reset_row(user, token), spend_rowcount=0, candidate=token)
    # A valid reset token whose digest was never stored — a guess, or a link for
    # an account that has since been deleted.
    stranger = create_refresh_token(
        user.id,
        settings=settings,
        extra_claims={"jti": str(uuid.uuid4()), "purpose": "password_reset"},
    )
    unknown_digest = await _message_for(row=None, spend_rowcount=0, candidate=stranger)
    # Not a reset token at all, and not a token of the right kind — both are
    # refused before the lookup, on purpose.
    wrong_purpose = await _message_for(row=None, spend_rowcount=1, candidate=not_a_reset)
    wrong_type = await _message_for(row=None, spend_rowcount=1, candidate=access)

    messages = {unknown, lost, unknown_digest, wrong_purpose, wrong_type}
    assert len(messages) == 1, messages
    assert messages.pop() == _INVALID_RESET


@pytest.mark.integration
async def test_a_reset_link_cannot_be_redeemed_twice_end_to_end(client):
    """The redemption is refused the second time, and it sets no password.

    The account ends up on the *first* redemption's password, which is the
    whole point: a replay of a stolen link must not be able to overwrite what
    the legitimate owner just set. Runs against the real database.
    """
    from tests.test_sessions import ADA, _account

    await _account(client, ADA)
    requested = await client.post("/api/v1/auth/password/forgot", json={"email": ADA["email"]})
    assert requested.status_code == 202, requested.text
    raw_token = requested.json()["dev_token"]

    first = await client.post(
        "/api/v1/auth/password/reset", json={"token": raw_token, "new_password": "First-Take-9"}
    )
    replay = await client.post(
        "/api/v1/auth/password/reset", json={"token": raw_token, "new_password": "Second-Take-9"}
    )

    assert first.status_code == 204, first.text
    assert replay.status_code == 401, replay.text
    # The first redemption's password is the one in force: the replay never got
    # to write one of its own, which is the whole point of the claim.
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": ADA["email"], "password": "First-Take-9"}
        )
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": ADA["email"], "password": "Second-Take-9"},
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": ADA["email"], "password": ADA["password"]},
        )
    ).status_code == 401


# ---------------------------------------------------------------------------
# 3. Revoking a session invalidates the access tokens minted from it
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _client_for(
    *, session_repository, user: User
) -> AsyncIterator[tuple[AsyncClient, FakeAsyncSession]]:
    """A client over the real app with every database dependency replaced.

    ``get_db`` is overridden as well as the two repositories, so nothing in the
    graph can reach for an engine: the app is built, the routes are the real
    ones, the exception handlers are the real ones, and the only fakes are the
    two repositories whose rows this test is about.
    """
    application = create_app(Settings(_env_file=None, environment="test", debug=False))
    session = FakeAsyncSession()
    session.scalar = user

    async def _db():
        yield session

    application.dependency_overrides[get_db] = _db
    application.dependency_overrides[get_user_repository] = lambda: _StubUserRepository(user)
    application.dependency_overrides[get_session_repository] = lambda: session_repository

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://nexus.test") as client:
        yield client, session


def _access_token(user: User, session_id: uuid.UUID) -> str:
    """An access token signed with the settings the application itself resolves.

    The dependencies call ``decode_token`` with no explicit settings, so they
    verify against the process-wide singleton. A token minted from a private
    :class:`Settings` would simply fail to decode, and the test would be
    asserting on the wrong 401.
    """
    return create_access_token(
        user.id,
        settings=get_settings(),
        extra_claims={"sid": str(session_id), "jti": str(uuid.uuid4())},
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_revoked_session_refuses_the_access_token_it_minted(
    assert_error_envelope,
):
    """The centrepiece: "that device is signed out" has to become true at once.

    A JWT stays cryptographically valid until it expires, so checking only the
    in-memory denylist left every access token already minted from the session
    authorising requests for the whole access-token window. The row in the
    database is the control, and this drives it through the real dependency.
    """
    user = _user()
    row = _session_row(
        user_id=user.id, token_hash=hash_token("refresh"), revoked_at=datetime.now(UTC)
    )
    token = _access_token(user, row.id)

    async with _client_for(session_repository=_StubSessionRepository(row=row), user=user) as (
        client,
        _,
    ):
        response = await client.get("/api/v1/auth/me", headers=_bearer(token))

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["message"] == _SESSION_NOT_LIVE


async def test_a_live_session_still_authorises_the_same_token():
    """The control: the check must not refuse a session that is still signed in.

    Without this the 401 above would prove nothing — a dependency that refused
    everything would pass it too.
    """
    user = _user()
    row = _session_row(user_id=user.id, token_hash=hash_token("refresh"))
    token = _access_token(user, row.id)

    async with _client_for(session_repository=_StubSessionRepository(row=row), user=user) as (
        client,
        _,
    ):
        response = await client.get("/api/v1/auth/me", headers=_bearer(token))

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(user.id)


async def test_a_missing_session_row_refuses_the_token(assert_error_envelope):
    """A token naming no row is refused, exactly as a revoked one is.

    The row can be gone because it expired, was purged, or belongs to a
    different account — none of which the caller may be able to tell apart.
    """
    user = _user()
    token = _access_token(user, uuid.uuid4())

    async with _client_for(session_repository=_StubSessionRepository(row=None), user=user) as (
        client,
        _,
    ):
        response = await client.get("/api/v1/auth/me", headers=_bearer(token))

    assert_error_envelope(response, status_code=401, code="unauthorized")


async def test_a_revoked_row_and_a_missing_row_are_indistinguishable(assert_error_envelope):
    """The same request against two different worlds must produce one answer.

    If the missing-row case said "unknown session" and the revoked case said
    "revoked", the difference is a working oracle for which session ids are
    real — which is precisely what the session id in a token would give an
    attacker.
    """
    user = _user()
    revoked = _session_row(
        user_id=user.id, token_hash=hash_token("refresh"), revoked_at=datetime.now(UTC)
    )
    gone = _session_row(user_id=user.id, token_hash=hash_token("refresh"))

    async with _client_for(session_repository=_StubSessionRepository(row=revoked), user=user) as (
        client,
        _,
    ):
        was_revoked = await client.get(
            "/api/v1/auth/me", headers=_bearer(_access_token(user, revoked.id))
        )
    async with _client_for(session_repository=_StubSessionRepository(row=None), user=user) as (
        client,
        _,
    ):
        never_existed = await client.get(
            "/api/v1/auth/me", headers=_bearer(_access_token(user, gone.id))
        )

    assert was_revoked.status_code == never_existed.status_code == 401
    assert was_revoked.json()["error"]["code"] == never_existed.json()["error"]["code"]
    assert was_revoked.json()["error"]["message"] == never_existed.json()["error"]["message"]


async def test_the_liveness_lookup_is_scoped_by_the_callers_own_user_id():
    """The row is resolved with ``get_by_id_for_user``, not by id alone.

    A lookup scoped by the subject the signed token already established is free
    defence: a future change to how the ``sid`` claim is built cannot turn this
    into a cross-account read, and the argument list is where that shows up.
    """
    user = _user()
    row = _session_row(user_id=user.id, token_hash=hash_token("refresh"))
    repository = _StubSessionRepository(row=row)

    async with _client_for(session_repository=repository, user=user) as (client, _):
        await client.get("/api/v1/auth/me", headers=_bearer(_access_token(user, row.id)))

    assert repository.ownership_lookups == [(row.id, user.id)]


@pytest.mark.integration
async def test_revoking_a_session_invalidates_its_access_token_end_to_end(
    client, assert_error_envelope
):
    """The same assertion as the unit test above, over the real thing.

    A real sign-in, a real row, and the revocation done by the API itself, so
    nothing about the 401 depends on how the dependency was wired. Runs against
    the real database.
    """
    from tests.test_sessions import ADA, _account, _sign_in

    await _account(client, ADA)
    tokens = await _sign_in(client, ADA)
    revoked = await _sign_in(client, ADA)

    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(revoked["access_token"]))
    ).status_code == 200
    deleted = await client.delete(
        f"/api/v1/auth/sessions/{revoked['session_id']}",
        headers=_bearer(tokens["access_token"]),
    )
    assert deleted.status_code == 204

    assert_error_envelope(
        await client.get("/api/v1/auth/me", headers=_bearer(revoked["access_token"])),
        status_code=401,
        code="unauthorized",
    )


# ---------------------------------------------------------------------------
# 4. A bearer-only logout ends the session
# ---------------------------------------------------------------------------


def _token_pair(user: User, settings: Settings, row: Session) -> tuple[str, str]:
    extra = {"sid": str(row.id), "jti": str(uuid.uuid4())}
    return (
        create_access_token(user.id, settings=settings, extra_claims=extra),
        create_refresh_token(user.id, settings=settings, extra_claims=extra),
    )


async def test_logging_out_with_an_access_token_ends_the_session():
    """The bug: an access token can never match ``token_hash``.

    That column holds the *refresh* digest, so the fingerprint check failed for
    every bearer-only logout, the failure was swallowed by design, and the
    client walked away from a ``204`` with its session still live. The ``sid``
    claim is inside a signed JWT, so using it is not a new trust boundary.
    """
    settings = _settings()
    user = _user()
    row = _session_row(
        user_id=user.id,
        token_hash=hash_token("the-refresh-digest"),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    access, _ = _token_pair(user, settings, row)
    repository = _StubSessionRepository(row=row)
    service = SessionService(repository, settings)

    await service.revoke_by_token(access)

    assert repository.revoke_calls == [row], "the row must actually be revoked"
    assert row.revoked_at is not None
    # The access token's digest is not what the row holds, which is the whole
    # reason the fallback has to exist.
    assert row.token_hash == hash_token("the-refresh-digest")


async def test_logging_out_with_a_refresh_token_ends_the_session_too():
    """The original path still works: the presented token matches the digest."""
    settings = _settings()
    user = _user()
    row = _session_row(
        user_id=user.id, token_hash="", expires_at=datetime.now(UTC) + timedelta(days=7)
    )
    _, refresh = _token_pair(user, settings, row)
    row.token_hash = hash_token(refresh)
    repository = _StubSessionRepository(row=row)
    service = SessionService(repository, settings)

    await service.revoke_by_token(refresh)

    assert repository.revoke_calls == [row]
    assert row.revoked_at is not None


async def test_a_token_that_is_not_a_session_token_is_swallowed():
    """Logout is best-effort by design: garbage in, no exception out.

    A client that cannot log out is a client that stays signed in, so nothing
    about an unparseable bearer is allowed to become a 401.
    """
    user = _user()
    row = _session_row(user_id=user.id, token_hash=hash_token("refresh"))
    repository = _StubSessionRepository(row=row)
    service = SessionService(repository, _settings())

    for candidate in ("", "not-a-jwt", "a.b.c"):
        await service.revoke_by_token(candidate)

    assert repository.revoke_calls == []


async def test_a_session_owned_by_somebody_else_is_not_revoked():
    """The fallback lookup is scoped by the signed subject.

    ``sid`` and ``sub`` are both inside the signature, so a caller cannot put an
    unsigned session id into a token this service accepts — and the lookup
    passes that subject anyway, so a row belonging to another account does not
    resolve and nothing is revoked.
    """
    settings = _settings()
    owner = _user()
    stranger = _user()
    row = _session_row(user_id=owner.id, token_hash=hash_token("refresh"))
    access, _ = _token_pair(stranger, settings, row)
    repository = _StubSessionRepository(row=row)
    service = SessionService(repository, settings)

    await service.revoke_by_token(access)

    assert repository.ownership_lookups == [(row.id, stranger.id)]
    assert repository.revoke_calls == [], "another account's session must survive"
    assert row.revoked_at is None


@pytest.mark.integration
async def test_logging_out_with_an_access_token_ends_the_session_end_to_end(client, db_session):
    """The real endpoint, the real row.

    After a bearer-only logout the row is revoked and the access token no
    longer authenticates — the two halves of the fix, over HTTP. Runs against
    the real database.
    """
    from tests.test_sessions import ADA, _account, _sign_in

    await _account(client, ADA)
    tokens = await _sign_in(client, ADA)

    response = await client.post("/api/v1/auth/logout", headers=_bearer(tokens["access_token"]))
    assert response.status_code == 204, response.text

    row = await db_session.get(Session, uuid.UUID(tokens["session_id"]))
    assert row.revoked_at is not None
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    ).status_code == 401


# ---------------------------------------------------------------------------
# 5. The absolute lifetime no longer slides
# ---------------------------------------------------------------------------


async def _rotate_and_capture_expiry(*, now_margin: timedelta | None = None):
    """Rotate a session that is ``now_margin`` old and return the expiry it was given.

    The value under test is the one handed to the conditional ``UPDATE``, so
    this drives the whole of :meth:`SessionService.rotate` — decode, liveness
    check, token minting and all — with the repository standing in for the
    database.
    """
    settings = _settings()
    user = _user()
    created = datetime.now(UTC) - (now_margin or timedelta())
    row = _session_row(
        user_id=user.id,
        token_hash="",
        created_at=created,
        expires_at=created + timedelta(days=30),
    )
    refresh = create_refresh_token(
        user.id, settings=settings, extra_claims={"sid": str(row.id), "jti": "jti-5"}
    )
    row.token_hash = hash_token(refresh)
    session = FakeAsyncSession()
    session.scalar = user
    repository = _StubSessionRepository(row=row, session=session)
    await SessionService(repository, settings).rotate(refresh_token=refresh)
    return row, repository.rotation_calls[0]["expires_at"], created


async def test_a_rotation_cannot_push_a_session_past_its_sign_in_deadline():
    """A near-thirty-day-old session is not given another thirty days.

    ``session_absolute_lifetime_days`` is documented as a hard ceiling on the
    age of the row. A limit measured from "now" on every refresh is not an
    absolute lifetime at all — it is the refresh window again, and an attacker
    who rotates a stolen token often enough never reaches it.
    """
    _, expires_at, created = await _rotate_and_capture_expiry(
        now_margin=timedelta(days=29, hours=23)
    )

    deadline = created + timedelta(days=30)
    assert expires_at <= deadline, "the absolute lifetime must not slide"
    # And it really was the clamp that bound it: the token's own window would
    # have put it seven days out.
    assert expires_at < datetime.now(UTC) + timedelta(days=1)


async def test_a_fresh_session_still_gets_the_token_window():
    """The clamp is a ``min``, not a hard 30-day wall: a new session is unaffected."""
    _, expires_at, _ = await _rotate_and_capture_expiry(now_margin=timedelta(minutes=1))

    expected = datetime.now(UTC) + timedelta(days=7)
    assert expected - timedelta(seconds=5) < expires_at < expected


async def test_a_naive_created_at_is_read_as_utc():
    """A row assembled in memory may be naive; treating it as local expires it early."""
    settings = _settings()
    service = SessionService(_StubSessionRepository(row=None), settings)
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=29)
    row = _session_row(
        user_id=_user().id,
        token_hash=UNRELATED_DIGEST,
        created_at=naive,
        expires_at=naive + timedelta(days=30),
    )

    expiry = service._rotated_expiry(row, datetime.now(UTC))

    assert expiry.tzinfo is not None
    assert expiry <= naive.replace(tzinfo=UTC) + timedelta(days=30)


# ---------------------------------------------------------------------------
# 6. Expired sessions are not listed as live
# ---------------------------------------------------------------------------


async def test_the_listing_filters_on_both_revocation_and_expiry():
    """An expired row is not a device that is signed in.

    The response schema has no ``expired`` marker, so listing one tells the user
    they are still signed in on hardware they abandoned months ago. The row is
    still kept; what changes is what "your sessions" means.
    """
    session = FakeAsyncSession()
    repository = SessionRepository(session)

    await repository.list_for_user(uuid.uuid4())

    sql = session.last_sql
    assert sql.startswith("SELECT")
    assert "sessions.user_id =" in sql
    assert "sessions.revoked_at IS NULL" in sql
    assert f"sessions.expires_at > {DATABASE_NOW}" in sql
    assert "ORDER BY sessions.created_at DESC" in sql


async def test_the_listing_and_the_eviction_order_query_agree():
    """Two ways of asking for "live" must not be able to disagree.

    ``list_live_ordered_by_created`` already had the expiry predicate and
    drives the session cap. A listing with a different definition of live would
    show the user a device the cap has already forgotten.
    """

    async def _where_of(call) -> str:
        session = FakeAsyncSession()
        await call(SessionRepository(session))
        sql = session.last_sql
        return sql[sql.index("WHERE") : sql.index("ORDER BY")].strip()

    listing = await _where_of(lambda repo: repo.list_for_user(uuid.uuid4()))
    eviction = await _where_of(lambda repo: repo.list_live_ordered_by_created(uuid.uuid4()))

    assert listing == eviction
    for predicate in ("revoked_at IS NULL", f"expires_at > {DATABASE_NOW}"):
        assert predicate in listing


async def test_the_history_variant_opts_out_of_both_filters():
    """``include_inactive`` is the way to ask for the history, and it is explicit."""
    session = FakeAsyncSession()
    repository = SessionRepository(session)

    await repository.list_for_user(uuid.uuid4(), include_inactive=True)

    sql = session.last_sql
    assert "sessions.user_id =" in sql
    assert "revoked_at IS NULL" not in sql
    assert "expires_at >" not in sql


@pytest.mark.integration
async def test_an_expired_session_is_not_listed(client, db_session):
    """The listing is of live sign-ins, so a lapsed row must not appear in it.

    Two devices are needed here, and the reason is itself a consequence of the
    fix for bug 3: an access token is only honoured while its own session row is
    live, so ageing the caller's *own* session would turn the request into a 401
    and prove nothing about the listing filter. Authenticating with one session
    while ageing a second isolates the filter under test.
    """
    from sqlalchemy import update

    from tests.test_sessions import ADA, _bearer, _sign_in

    await client.post("/api/v1/auth/register", json=ADA)
    caller = await _sign_in(client, ADA)
    lapsed = await _sign_in(client, ADA)

    await db_session.execute(
        update(Session)
        .where(Session.id == uuid.UUID(lapsed["session_id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db_session.commit()

    response = await client.get("/api/v1/auth/sessions", headers=_bearer(caller["access_token"]))

    assert response.status_code == 200, response.text
    body = response.json()
    listed = [row["id"] for row in body["sessions"]]
    assert lapsed["session_id"] not in listed
    assert caller["session_id"] in listed
    assert body["current_id"] == caller["session_id"]


# ---------------------------------------------------------------------------
# 9. A failed audit write does not poison the request's session
# ---------------------------------------------------------------------------


async def test_a_failed_audit_write_rolls_the_shared_session_back():
    """The rollback is the fix, and the reason is ``PendingRollbackError``.

    ``AuditRepository`` shares the request-scoped ``AsyncSession`` with every
    other repository in the request and commits on it. When that commit fails,
    SQLAlchemy leaves the session inside a transaction it has already rolled
    back, and every later statement on it raises before it reaches the database
    — so swallowing the audit failure without undoing it turns one broken audit
    row into a 500 for whatever the caller did next.
    """
    session = FakeAsyncSession()
    service = AuditService(_FailingAuditRepository(session))

    await service.record(AuditEvent.USER_LOGIN)

    assert session.rollbacks == 1


async def test_a_failed_audit_write_returns_none_rather_than_raising():
    """Audit is observability, not business logic; it must not deny service."""
    service = AuditService(_FailingAuditRepository(FakeAsyncSession()))

    assert await service.record(AuditEvent.ACCOUNT_DELETED, user_id=uuid.uuid4()) is None


async def test_a_successful_audit_write_leaves_the_session_alone():
    """The rollback is a recovery step, not something to do on the happy path."""
    session = FakeAsyncSession()
    logged: list[dict] = []

    class _Working:
        def __init__(self) -> None:
            self.session = session

        async def record(self, **fields) -> object:
            logged.append(fields)
            return object()

    await AuditService(_Working()).record(AuditEvent.USER_LOGIN)

    assert logged, "the event must reach the repository"
    assert session.rollbacks == 0


async def test_a_rollback_that_itself_fails_is_still_swallowed():
    """Recovery must not re-raise. There is nothing left to salvage by then."""
    session = FakeAsyncSession()

    async def _boom() -> None:
        raise RuntimeError("connection is gone")

    session.rollback = _boom
    service = AuditService(_FailingAuditRepository(session))

    assert await service.record(AuditEvent.USER_LOGIN) is None


async def test_a_broken_audit_sink_cannot_deny_a_registration():
    """The end of the chain: the business operation still completes.

    Registering is a write through the same session, and a service that
    propagated the audit error would refuse the sign-up — an attacker who could
    fill the audit table would take authentication down for everybody.
    """
    user_repository = _StubUserRepository(_user())
    session = FakeAsyncSession()

    class _RegisteringRepository(_StubUserRepository):
        async def create(self, **fields):
            created = self._user
            for key, value in fields.items():
                setattr(created, key, value)
            return created

    repository = _RegisteringRepository(user_repository._user, session=session)
    broken_audit = AuditService(_FailingAuditRepository(session))
    service = AuthService(repository, None, broken_audit, _settings())

    from app.schemas.user import UserCreate

    created = await service.register(
        UserCreate(username="ada", email="ada@nexus.dev", password=ACCOUNT_PASSWORD)
    )

    assert created.id == repository._user.id
    assert session.rollbacks >= 1, "the failed audit write must have restored the session"
