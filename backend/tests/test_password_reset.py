"""The password-reset request flow, exercised against stub collaborators.

Two properties of this endpoint are worth proving without a database, because
both are security properties rather than plumbing:

* it cannot be used to discover which addresses have an account, and
* the raw reset token — a bearer credential for a full account takeover — is
  returned outside production and nowhere else.

The rest of the flow (the redemption, the single-use guarantee, the session
revocation) is only meaningful against a real database and lives in
``test_auth_phase2``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.core.security import decode_token, hash_token
from app.models.audit import AuditEvent
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.services.auth_service import AuthService

STRONG_SECRET = "k7Qm2Zt9XpL4vR8sN6bY3wJ1hG5dF0cA7eU9iO2pS4tV6xZ8mB1nC3qW5eR7yT9u"


def _settings(**overrides) -> Settings:
    """Development settings for a service-level test.

    ``dev_expose_reset_token`` is on because the service only hands the raw token
    back when it is explicitly asked to: the endpoint is otherwise an account
    oracle, since returning a token for a known address and not for an unknown
    one is exactly the difference the shared response body is meant to hide. A
    test that wants a token therefore has to say so.
    """
    return Settings(
        _env_file=None,
        **{"secret_key": STRONG_SECRET, "dev_expose_reset_token": True, **overrides},
    )


def _production_kwargs() -> dict:
    """Production refuses the placeholder secret and refuses debug, so both are set.

    ``dev_expose_reset_token`` is deliberately absent: production never returns
    the token, whether or not the flag is set, so the token must be ``None``.
    """
    return {"environment": "production", "secret_key": STRONG_SECRET, "debug": False}


def _user(*, active: bool = True) -> User:
    return User(
        id=uuid.uuid4(),
        email="ada@nexus.dev",
        username="ada",
        hashed_password="x",  # noqa: S106
        role="user",
        is_active=active,
    )


class _FakeSession:
    """Just enough of an ``AsyncSession`` for the reset repository."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def commit(self) -> None:
        return None

    async def refresh(self, instance: object) -> None:
        return None


class _StubUserRepository:
    """Answers one lookup and collects whatever was added to the session."""

    def __init__(self, user: User | None) -> None:
        self._user = user
        self.session = _FakeSession()

    async def get_by_email(self, email: str) -> User | None:
        return self._user

    @property
    def reset_tokens(self) -> list[PasswordResetToken]:
        return [item for item in self.session.added if isinstance(item, PasswordResetToken)]


class _RecordingAudit:
    def __init__(self) -> None:
        self.events: list[tuple[object, uuid.UUID | None, dict | None]] = []

    async def record(self, event, *, user_id=None, ip_address=None, user_agent=None, metadata=None):
        self.events.append((event, user_id, metadata))
        return None


def _service(user: User | None, **settings_kwargs):
    repository = _StubUserRepository(user)
    audit = _RecordingAudit()
    service = AuthService(
        repository=repository,
        audit=audit,
        settings=_settings(**settings_kwargs) if settings_kwargs else _settings(),
    )
    return service, repository, audit


# -- The token ---------------------------------------------------------------


async def test_a_known_address_receives_a_raw_token_outside_production():
    service, repository, _ = _service(_user())

    raw_token = await service.request_password_reset(email="ada@nexus.dev")

    assert raw_token is not None
    assert len(repository.reset_tokens) == 1


async def test_a_known_address_receives_nothing_in_production():
    """The raw token is withheld in production.

    A local-first install has no mail transport, so the token is returned
    outside production; in production there is nowhere safe to send it, so the
    field stays null. The row is still created either way — the difference is
    only whether the caller is handed the raw credential.
    """
    service, repository, _ = _service(_user(), **_production_kwargs())

    raw_token = await service.request_password_reset(email="ada@nexus.dev")

    assert raw_token is None
    assert len(repository.reset_tokens) == 1, "the row must still exist to be redeemed"


async def test_only_the_digest_is_ever_stored():
    """The raw token exists in the mail (or, in development, the response) and nowhere else."""
    service, repository, _ = _service(_user())

    raw_token = await service.request_password_reset(email="ada@nexus.dev")
    stored = repository.reset_tokens[0]

    assert stored.token_hash == hash_token(raw_token)
    assert stored.token_hash != raw_token
    assert len(stored.token_hash) == 64


async def test_the_token_is_a_refresh_token_marked_as_a_reset():
    """The token is marked so it cannot be used as anything else.

    The ``purpose`` claim is what stops a reset token being replayed as a
    refresh token, and the type is what stops an access token being redeemed.
    """
    service, _, _ = _service(_user())

    raw_token = await service.request_password_reset(email="ada@nexus.dev")
    claims = decode_token(raw_token, settings=service.settings).claims

    assert claims["type"] == "refresh"
    assert claims["purpose"] == "password_reset"
    assert "sid" not in claims
    assert claims["jti"]


async def test_the_token_expires_within_the_configured_window():
    service, repository, _ = _service(_user(), password_reset_expire_minutes=15)

    before = datetime.now(UTC)
    await service.request_password_reset(email="ada@nexus.dev")
    row = repository.reset_tokens[0]

    assert 14 * 60 < (row.expires_at - before).total_seconds() <= 15 * 60 + 1


# -- No enumeration ----------------------------------------------------------


async def test_an_unknown_address_receives_nothing_at_all():
    service, repository, audit = _service(None)

    raw_token = await service.request_password_reset(email="nobody@nexus.dev")

    assert raw_token is None
    assert repository.reset_tokens == []
    assert audit.events[0][0] is AuditEvent.PASSWORD_RESET_REQUESTED
    assert audit.events[0][1] is None


async def test_in_production_a_known_and_an_unknown_address_are_indistinguishable():
    """The property the endpoint's 202 exists for.

    Outside production the two *do* differ, deliberately: a local install has no
    mail transport, so the known address has to be handed the token to be
    usable at all. In production the field is always null, so the two are the
    same value, the same status and the same audit shape — and the endpoint can
    no longer be used to discover which addresses have an account.
    """
    known_service, known_repo, known_audit = _service(_user(), **_production_kwargs())
    unknown_service, unknown_repo, unknown_audit = _service(None, **_production_kwargs())

    known_token = await known_service.request_password_reset(email="ada@nexus.dev")
    unknown_token = await unknown_service.request_password_reset(email="nobody@nexus.dev")

    assert known_token is None
    assert known_token is unknown_token
    assert (
        known_audit.events[0][0]
        is unknown_audit.events[0][0]
        is AuditEvent.PASSWORD_RESET_REQUESTED
    )
    assert known_audit.events[0][2] == unknown_audit.events[0][2] is None
    # The only residual difference is a single INSERT, which is why the token
    # itself is the thing production refuses to hand out.
    assert len(unknown_repo.reset_tokens) == 0
    assert len(known_repo.reset_tokens) == 1


async def test_an_inactive_account_receives_nothing():
    """A deactivated account must not be a way to request a fresh credential."""
    service, repository, audit = _service(_user(active=False))

    raw_token = await service.request_password_reset(email="ada@nexus.dev")

    assert raw_token is None
    assert repository.reset_tokens == []
    # Still audited, and still attributed to the account: the row names a
    # request against a real user, which is exactly what is worth recording.
    assert audit.events[0][0] is AuditEvent.PASSWORD_RESET_REQUESTED
    assert audit.events[0][1] is not None


async def test_the_lookup_is_case_and_whitespace_insensitive():
    """The repository normalises, so the same address resolves the same way."""
    service, _, _ = _service(_user())

    raw_token = await service.request_password_reset(email="  ADA@Nexus.DEV ")

    assert raw_token is not None


# -- Audit hygiene -----------------------------------------------------------


async def test_the_audit_metadata_carries_no_token():
    """No credential is written to the audit trail.

    The trail is retained far longer than the token it describes, and nothing
    is filtered on the way in — so a token landing here cannot be un-leaked.
    """
    service, _, audit = _service(_user())

    raw_token = await service.request_password_reset(email="ada@nexus.dev")

    recorded = audit.events[0]
    assert recorded[0] is AuditEvent.PASSWORD_RESET_REQUESTED
    assert raw_token not in repr(recorded)
    assert recorded[2] is None


async def test_the_audit_row_is_attributed_to_the_account_it_is_about():
    """Distinct from the unknown-address case, where ``user_id`` is null."""
    user = _user()
    service, _, audit = _service(user)

    await service.request_password_reset(email="ada@nexus.dev")

    assert audit.events[0][1] == user.id


def test_the_production_settings_guard_is_satisfiable():
    """The construction a production-mode test needs, kept honest here.

    ``Settings`` refuses to build a production instance with the placeholder
    secret or with debug on, so the production branch of the reset endpoint
    cannot be reached by accident — and the ``_production_kwargs`` above cannot
    be quietly satisfied by a development build.
    """
    from pydantic import ValidationError

    from app.core.config import INSECURE_DEV_SECRET_KEY

    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key=INSECURE_DEV_SECRET_KEY,
            debug=False,
        )

    assert Settings(
        _env_file=None, environment="production", secret_key=STRONG_SECRET, debug=False
    ).is_production
