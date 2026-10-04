"""Authentication business logic.

Registration, credential verification, token issuance, rotation and revocation,
orchestrating device sessions and the audit trail. Cryptography and claim
validation live in ``app.core.security``; the rules about *when* a token may be
issued or reused live here.

Routers translate the exceptions raised here into HTTP responses — this module
never imports FastAPI.

The collaborators are optional in the constructor. That is what keeps the
Phase 1 call sites working, where a service was built from a user repository
alone, and it gives a caller without session storage a degraded but coherent
service: tokens are still signed and still single-use, they simply have no row
behind them and therefore cannot be listed or revoked per device. The session
path is the production path; the fallback exists so the token rules can be
exercised without one.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.logging import log_event
from app.core.security import (
    TokenData,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    new_session_id,
    verify_password,
)
from app.models.audit import AuditEvent
from app.models.user import User
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.user import UserRepository
from app.schemas.user import TokenPair, TokenRefresh, UserCreate, UserLogin
from app.services.audit_service import AuditService
from app.services.session_service import SessionService

__all__ = ["AuthService", "RevocationStore", "get_revocation_store"]

logger = logging.getLogger("app.services.auth_service")

#: Generic message for every failed credential check, so that probing cannot
#: distinguish "unknown email" from "wrong password".
_INVALID_CREDENTIALS = "Incorrect email or password."
_REVOKED = "This token has been revoked."
_EMAIL_TAKEN = "An account with this email already exists."
_USERNAME_TAKEN = "That username is already taken."
_INVALID_RESET = "This password reset link is invalid or has expired."
_WRONG_CURRENT_PASSWORD = "The current password is incorrect."
_SAME_PASSWORD = "The new password must be different from the current one."

#: Distinguishes a password-reset token from a refresh token.
#:
#: ``TokenType`` has two members and adding a third would change a persisted
#: contract, so a reset token is minted as the long-lived kind with this claim
#: attached, and every path that accepts a refresh token refuses it. It carries
#: no ``sid``, which is the backstop: a reset token cannot be rotated, and it is
#: rejected by the fingerprint check because no session row holds its digest.
_RESET_PURPOSE = "password_reset"


class RevocationStore:
    """Denylist of revoked token ids, keyed by the token's ``jti`` claim.

    Phase 1 runs a single process with no shared cache, so revocations live in
    memory and are dropped once the token would have expired anyway. Expired
    entries are purged on every call, so the dict only ever holds revocations
    that are still live. The interface is deliberately narrow so a later phase
    can back it with Redis without touching the auth service.
    """

    def __init__(self) -> None:
        self._revoked: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        """Blacklist a token id until it would have expired anyway.

        Purging here as well as on lookup keeps the store bounded when the only
        traffic is revocations — a logout-only workload never reads it back.
        """
        async with self._lock:
            self._purge_expired()
            self._revoked[jti] = expires_at

    async def is_revoked(self, jti: str) -> bool:
        """Report whether a token id has been revoked."""
        async with self._lock:
            self._purge_expired()
            return jti in self._revoked

    def _purge_expired(self) -> None:
        now = datetime.now(UTC)
        for jti, expires_at in list(self._revoked.items()):
            if expires_at <= now:
                del self._revoked[jti]


def _jti(claims: dict[str, object]) -> str:
    """Return the token id, or raise for a token this service did not mint."""
    value = claims.get("jti")
    if not isinstance(value, str) or not value:
        raise UnauthorizedError(_REVOKED)
    return value


#: A bcrypt hash of a random value, computed once when this module is imported.
#:
#: ``authenticate`` verifies against it when the address is unknown, so that the
#: missing-account path pays the same bcrypt cost as a real check: the shared
#: error message hides the account from the response text, and this hides it
#: from the response clock as well. The value behind the hash is random and
#: discarded, so no submitted password can ever match it.
#:
#: Computed at import rather than lazily cached on first use. A lazy cache pays
#: its hash *and* its verify on the first unknown-address login — ~410 ms
#: against ~200 ms for a known one — so the very first probe of a freshly
#: started process is the one probe the decoy exists to make indistinguishable.
#: The cost moves to import, where it is paid once and off the request path.
_DECOY_HASH = hash_password(secrets.token_urlsafe(32))


class AuthService:
    """Registration, credential verification, token lifecycle and password reset."""

    def __init__(
        self,
        repository: UserRepository,
        sessions: SessionService | None = None,
        audit: AuditService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.sessions = sessions
        self.audit = audit
        self.settings = settings or get_settings()

    # -- Token helpers -------------------------------------------------------

    def issue_token_pair(self, user: User, *, session_id: str | None = None) -> TokenPair:
        """Mint a fresh access/refresh pair for a user.

        Used only by the fallback path, where no session store was supplied and
        therefore no ``sid`` can be named. :class:`SessionService` builds its own
        pair, because it is the only one that knows which row it just wrote.

        Each token gets its own ``jti``. They share a subject and a lifetime, so
        without one they would be byte-identical inside a single clock second —
        and revoking one would silently revoke the other.
        """
        shared: dict[str, object] = {"sid": session_id} if session_id is not None else {}
        return TokenPair(
            access_token=create_access_token(
                user.id,
                settings=self.settings,
                extra_claims={"jti": new_session_id(), **shared},
            ),
            refresh_token=create_refresh_token(
                user.id,
                settings=self.settings,
                extra_claims={"jti": new_session_id(), **shared},
            ),
            expires_in=self.settings.access_token_expire_minutes * 60,
            session_id=uuid.UUID(session_id) if session_id is not None else None,
        )

    async def is_revoked(self, token_data: TokenData) -> bool:
        """Report whether the presented token was revoked."""
        return await get_revocation_store().is_revoked(_jti(token_data.claims))

    # -- Flows ---------------------------------------------------------------

    async def register(self, data: UserCreate) -> User:
        """Create an account, rejecting identifiers that are already taken.

        Both uniqueness rules are enforced twice, for the same reason: the
        pre-check answers the common case with a clean 409, and the commit is
        guarded as well so that losing the race against a concurrent
        registration produces the same 409 instead of escaping as a driver error.

        Args:
            data: The registration payload.

        Returns:
            The new account.

        Raises:
            ConflictError: If the email or the username is already registered.
        """
        if await self.repository.exists_by_email(str(data.email)):
            raise ConflictError(_EMAIL_TAKEN)
        username = _clean_username(data.username)
        if await self.repository.exists_by_username(username):
            raise ConflictError(_USERNAME_TAKEN)
        try:
            user = await self.repository.create(
                email=str(data.email),
                # bcrypt is a ~200 ms solid compute. Run it on a worker thread so
                # a registration cannot stall the loop for every other request
                # in the process — the same rule the ML router applies to
                # inference.
                hashed_password=await asyncio.to_thread(hash_password, data.password),
                username=username,
                display_name=data.display_name,
            )
        except IntegrityError as exc:
            raise ConflictError(_conflict_message(exc, _EMAIL_TAKEN, _USERNAME_TAKEN)) from exc
        # Metadata stays minimal on purpose: the username is the public handle
        # this event is *about*, and the address is not recorded here because
        # nothing downstream of this row needs it — every other event that wants
        # an account points at ``user_id`` instead.
        await self._audit(
            AuditEvent.USER_REGISTERED,
            user_id=user.id,
            metadata={"username": username},
        )
        return user

    async def authenticate(
        self, data: UserLogin, *, user_agent: str | None = None, ip_address: str | None = None
    ) -> User:
        """Verify credentials and return the active user.

        The password is verified on *both* paths — against the decoy hash when
        the address is unknown. Short-circuiting on the lookup instead would
        answer an unknown address in ~1 ms against ~250 ms for a known one, and
        that difference is what the shared error message is meant to prevent.

        A failure is audited on both paths too, for the same reason: the record
        of a failed sign-in is the point of the trail, and an unknown address is
        recorded with a null ``user_id`` rather than dropped.

        **The refusal is logged as well as audited.** The audit row is the
        per-account trail; ``auth_login_failed`` is the line an operator greps for
        when a deployment is being probed, because a single account's audit history
        does not show a hundred different addresses arriving at once. The reason
        is the closed vocabulary ``invalid_credentials`` or ``inactive`` — the
        email, the password and the token pair are never in the line.

        The verify itself runs on a worker thread, so the ~250 ms it costs is
        this login's latency rather than the whole process's.
        """
        user = await self.repository.get_by_email(str(data.email))
        stored_hash = user.hashed_password if user is not None else _DECOY_HASH
        password_ok = await asyncio.to_thread(verify_password, data.password, stored_hash)
        if user is None or not password_ok:
            await self._audit(
                AuditEvent.USER_LOGIN_FAILED,
                user_id=user.id if user is not None else None,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self._log_failure("invalid_credentials", user, ip_address)
            raise UnauthorizedError(_INVALID_CREDENTIALS)
        if not user.is_active:
            self._log_failure("inactive", user, ip_address)
            raise UnauthorizedError("This account is inactive.")
        return user

    @staticmethod
    def _log_failure(reason: str, user: User | None, ip_address: str | None) -> None:
        """Emit one ``auth_login_failed`` line for a refused sign-in.

        Carries the caller's address because the middleware already writes
        ``client_ip`` on every completed request, so this adds no new personal
        data to the log — it makes the address findable from the event name
        alone rather than by correlating against an access line. The user agent
        is deliberately *not* repeated here; it is a column in the audit row and
        in the access log, and a third copy would be one more place to forget.

        Args:
            reason: Which of the two refusals this is.
            user: The account, or ``None`` when the address is unknown.
            ip_address: The caller's address, if the middleware resolved one.
        """
        log_event(
            logger,
            logging.WARNING,
            "auth_login_failed",
            reason=reason,
            user_id=str(user.id) if user is not None else None,
            ip_address=ip_address,
        )

    async def login(
        self,
        data: UserLogin,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        """Authenticate, stamp the login time, open a session and return a pair.

        The session is opened before the pair is returned so that the tokens the
        client is about to store are already backed by a row they can be revoked
        through. Without a session store configured, a plain token pair is issued
        instead — see the module docstring for why that path exists.

        The sign-in is audited before the session it produced, so a trail read
        top to bottom tells the story in the order it happened.
        """
        user = await self.authenticate(data, user_agent=user_agent, ip_address=ip_address)
        await self.repository.update_fields(user, last_login_at=datetime.now(UTC))
        await self._audit(
            AuditEvent.USER_LOGIN,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if self.sessions is None:
            return self.issue_token_pair(user)
        db_session, pair = await self.sessions.issue(
            user=user,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await self._audit(
            AuditEvent.SESSION_CREATED,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"session_id": str(db_session.id)},
        )
        return pair

    async def rotate(
        self,
        data: TokenRefresh,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        """Exchange a refresh token for a new pair, retiring the presented one.

        Rotation is single-use twice over when a session store is configured: the
        session row's digest is replaced, so the old token no longer matches it,
        and the token id is blacklisted in memory, so a stolen copy is refused
        even within the window in which both are valid. Either alone would be
        enough; together a race between two clients cannot spend the same token
        twice.
        """
        token_data = decode_token(
            data.refresh_token, expected_type=TokenType.REFRESH, settings=self.settings
        )
        if _purpose_of(token_data.claims) == _RESET_PURPOSE:
            raise UnauthorizedError(_REVOKED)
        if await self.is_revoked(token_data):
            raise UnauthorizedError(_REVOKED)
        if self.sessions is not None:
            _, pair = await self.sessions.rotate(
                refresh_token=data.refresh_token,
                user_agent=user_agent,
                ip_address=ip_address,
            )
            await self._revoke(token_data)
            return pair
        try:
            user_id = uuid.UUID(token_data.subject)
        except ValueError:
            raise UnauthorizedError("The authentication token is invalid.") from None
        user = await self.repository.get_by_id(user_id)
        if user is None:
            raise UnauthorizedError("The authentication token is invalid.")
        if not user.is_active:
            raise UnauthorizedError("This account is inactive.")
        await self._revoke(token_data)
        return self.issue_token_pair(user)

    async def revoke(self, token: str) -> None:
        """Blacklist a token and end the session behind it. Never raises.

        Unparseable or already expired tokens are ignored: logout answers 204
        whatever the caller presents, because a client that cannot log out is a
        client that stays signed in.

        The same rule covers the rest of the method, not just the decode. A
        signature that verifies but carries no ``jti`` has no id to blacklist,
        and a session row that cannot be written is a database problem rather
        than something the client could do anything about; either one escaping
        would turn logout into a 401 and leave the caller signed in — the
        opposite of what they asked for. Failures are logged at WARNING instead,
        so the condition is still visible to an operator without reaching the
        caller as an error.

        The denylist write is the *only* step guarded. It is the one that can
        fail for a reason of its own — a token this service did not mint has no
        ``jti`` to blacklist — and letting that failure decide the fate of the
        session row would answer 204 with the device still signed in. The row is
        revoked from the token's own claims, which need no ``jti``, so it is
        attempted regardless; so is the audit row, because the attempt happened
        either way.
        """
        try:
            token_data = decode_token(token, settings=self.settings)
        except UnauthorizedError:
            return
        try:
            await self._revoke(token_data)
        except Exception:
            logger.warning("logout_denylist_failed", exc_info=True)
        if self.sessions is not None:
            await self.sessions.revoke_by_token(token)
        # A logout is one of the few events an operator actually looks for, so
        # it is recorded even though the token itself is deliberately not: the
        # subject is enough to say "this account ended this session here". It
        # sits outside the guard above because the attempt happened whether or
        # not the write behind it succeeded, and that pairing — an audit row and
        # a WARNING — is what makes a failed sign-out diagnosable.
        await self._audit(AuditEvent.USER_LOGOUT, user_id=_subject_or_none(token_data))

    async def logout_all(
        self, *, user_id: uuid.UUID, keep_session_id: uuid.UUID | None = None
    ) -> int:
        """End every session for a user and return how many were ended.

        Args:
            user_id: Whose sessions to end.
            keep_session_id: The session the caller is using, when it wants to
                stay signed in here. ``None`` ends them all.

        Returns:
            The number of sessions revoked; ``0`` when no session store is
            configured, since there is nothing to revoke.
        """
        if self.sessions is None:
            return 0
        revoked = await self.sessions.revoke_all(user_id=user_id, keep_session_id=keep_session_id)
        await self._audit(
            AuditEvent.SESSIONS_REVOKED_ALL,
            user_id=user_id,
            metadata={
                "revoked": revoked,
                "kept": str(keep_session_id) if keep_session_id else None,
            },
        )
        return revoked

    async def change_password(
        self,
        *,
        user: User,
        current_password: str,
        new_password: str,
        keep_session_id: uuid.UUID | None = None,
    ) -> int:
        """Set a new password and end every other session.

        Why this lives here rather than in a router: a password change is only
        half a security control without the second half. Re-hashing the password
        leaves every other device signed in with a credential that the owner has
        just declared compromised. Revoking them is the point, and a router that
        forgot to call it would leave that gap open while still returning 200.

        The current password is required because a password change is also an
        account takeover when the session is stolen: without the check, anyone
        who finds an unlocked browser could lock the owner out permanently.

        Args:
            user: The account being changed.
            current_password: The password in force now.
            new_password: The replacement.
            keep_session_id: The caller's own session, when the API layer can
                identify it from the ``sid`` claim of the bearer. Optional so the
                method stays callable without one, in which case the caller is
                signed out too and must sign in again.

        Returns:
            The number of other sessions that were revoked.
        """
        # Three bcrypt operations, ~600 ms of solid compute — on the thread, not
        # on the loop.
        if not await asyncio.to_thread(verify_password, current_password, user.hashed_password):
            raise UnauthorizedError(_WRONG_CURRENT_PASSWORD)
        if await asyncio.to_thread(verify_password, new_password, user.hashed_password):
            raise ConflictError(_SAME_PASSWORD)
        now = datetime.now(UTC)
        await self.repository.update_fields(
            user,
            hashed_password=await asyncio.to_thread(hash_password, new_password),
            password_changed_at=now,
        )
        revoked = 0
        if self.sessions is not None:
            revoked = await self.sessions.revoke_all(
                user_id=user.id, keep_session_id=keep_session_id
            )
        await self._resets.invalidate_all_for_user(user.id, used_at=now)
        await self._audit(
            AuditEvent.PASSWORD_CHANGED,
            user_id=user.id,
            metadata={"revoked_sessions": revoked},
        )
        return revoked

    # -- Password reset ------------------------------------------------------

    async def request_password_reset(
        self,
        *,
        email: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> str | None:
        """Start a password reset and return the raw token only where asked.

        **This endpoint must not be an account oracle.** A known address and an
        unknown one produce the same audit row, the same response, and the same
        shape of work: one account lookup, one signature, one write. They differ
        only in whether the address had an active account to attach the token
        to. Returning 202 either way is what makes the endpoint safe to expose;
        the residual difference is a single INSERT, orders of magnitude below the
        bcrypt constant a login pays, and it is the price of storing a token at
        all.

        Handing the token back is opt-in through
        ``settings.dev_expose_reset_token``, and off by default. This is a
        local-first product, so a user with no mail provider configured has to be
        able to finish the flow — but "not production" is not the same thing as
        "safe to hand a takeover token to an unauthenticated caller", and a
        staging or preview deployment is not production either. The token is
        therefore only returned where the deployment has said, in as many words,
        that it wants it.

        Returns:
            The raw reset token when ``dev_expose_reset_token`` is set, or
            ``None`` for an address with no active account and for every
            deployment that has not opted in.
        """
        user = await self.repository.get_by_email(email)
        now = datetime.now(UTC)
        raw_token: str | None = None
        if user is not None and user.is_active:
            expires_at = now + timedelta(minutes=self.settings.password_reset_expire_minutes)
            raw_token = create_refresh_token(
                user.id,
                settings=self.settings,
                expires_delta=timedelta(minutes=self.settings.password_reset_expire_minutes),
                extra_claims={"jti": new_session_id(), "purpose": _RESET_PURPOSE},
            )
            await self._resets.create(
                user_id=user.id,
                token_hash=hash_token(raw_token),
                expires_at=expires_at,
            )
        await self._audit(
            AuditEvent.PASSWORD_RESET_REQUESTED,
            user_id=user.id if user is not None else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if raw_token is None or not self.settings.dev_expose_reset_token:
            return None
        return raw_token

    async def complete_password_reset(
        self,
        *,
        token: str,
        new_password: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Redeem a reset token, set the new password and end every session.

        Every session goes, including the one that requested the reset. A reset
        is the recovery path for a compromised account, so leaving a live session
        behind would leave the compromise in place with a new password. Every
        other outstanding reset link goes with it, for the same reason: a second
        link that survives can put the old password back afterwards.

        Args:
            token: The raw reset token from the link.
            new_password: The replacement.
            user_agent: Recorded on the audit row.
            ip_address: Recorded on the audit row.

        Raises:
            UnauthorizedError: If the token is unknown, spent, expired, was not
                a reset token at all, or lost the race to a concurrent
                redemption of the same link. All five answer identically, so a
                spent link cannot be distinguished from a guessed one.
        """
        now = datetime.now(UTC)
        token_data = decode_token(token, settings=self.settings)
        if not hmac.compare_digest(str(token_data.token_type), TokenType.REFRESH.value):
            raise UnauthorizedError(_INVALID_RESET)
        if _purpose_of(token_data.claims) != _RESET_PURPOSE:
            raise UnauthorizedError(_INVALID_RESET)
        _jti(token_data.claims)
        row = await self._resets.get_valid_by_token_hash(hash_token(token), now=now)
        if row is None:
            raise UnauthorizedError(_INVALID_RESET)
        user = await self.repository.get_by_id(row.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError(_INVALID_RESET)
        # The token is claimed before anything is hashed or written, not after.
        # Loading the row and then acting on it leaves two concurrent
        # redemptions of a stolen link both ready to overwrite the password the
        # legitimate owner just set; the conditional write in ``spend`` lets
        # exactly one of them through and hands the other this same message.
        # Spending first also fails the safe way: if the password write that
        # follows fails, the link is dead rather than still redeemable.
        if not await self._resets.spend(row.id, used_at=now):
            raise UnauthorizedError(_INVALID_RESET)
        # Every *other* outstanding link goes too, for the same reason
        # ``change_password`` spends them: one link is redeemed while a second
        # sits in someone else's inbox, and spending only the row that was used
        # leaves that one able to overwrite the password that was just set.
        await self._resets.invalidate_all_for_user(user.id, used_at=now)
        await self.repository.update_fields(
            user,
            hashed_password=await asyncio.to_thread(hash_password, new_password),
            password_changed_at=now,
        )
        revoked = 0
        if self.sessions is not None:
            revoked = await self.sessions.revoke_all(user_id=user.id)
        await self._audit(
            AuditEvent.PASSWORD_RESET_COMPLETED,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"revoked_sessions": revoked},
        )

    # -- Internals -----------------------------------------------------------

    async def _revoke(self, token_data: TokenData) -> None:
        expires_at = token_data.expires_at or datetime.now(UTC) + timedelta(seconds=1)
        await get_revocation_store().revoke(_jti(token_data.claims), expires_at)

    async def _audit(
        self,
        event: AuditEvent,
        *,
        user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record an audit event, or do nothing when no trail is configured.

        Never raises: :meth:`AuditService.record` is best-effort by design, and
        this wrapper only adds the "no audit sink" case to the same rule.
        """
        if self.audit is None:
            return
        await self.audit.record(
            event,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata,
        )

    @property
    def _resets(self) -> PasswordResetRepository:
        """The reset-token repository, bound to the same request-scoped session.

        Built on demand from the user repository's own
        :class:`~sqlalchemy.ext.asyncio.AsyncSession` rather than injected: the
        constructor signature is part of the API layer's contract, and a reset
        row and the account it belongs to must be read and written through one
        connection to be one consistent unit of work.
        """
        return PasswordResetRepository(self.repository.session)


def _clean_username(username: str) -> str:
    """Return the username in the form the uniqueness index compares against.

    Trimmed, but deliberately *not* folded to lower case. A username is shown
    back to its owner and the index compares it verbatim, so folding it here
    would both make ``Ada`` and ``ada`` collide in a way the user cannot see and
    sign a user out of the handle they registered with. The value stored has to
    be the value the lookup compares against, whichever layer writes it.
    """
    return username.strip()


def _purpose_of(claims: dict[str, object]) -> str | None:
    """Return a token's ``purpose`` claim, or ``None`` when it has none."""
    value = claims.get("purpose")
    return value if isinstance(value, str) else None


def _conflict_message(exc: IntegrityError, email_message: str, username_message: str) -> str:
    """Report which unique constraint lost the race, when the driver says so.

    The driver names the index that rejected the row, so a concurrent duplicate
    username can be answered with the username message rather than a misleading
    one about the email. Anything that does not name a constraint — a different
    driver, a proxy, a future constraint — falls back to the primary message
    rather than to a guess.
    """
    constraint = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(constraint, "constraint_name", None) or ""
    if "username" in name:
        return username_message
    return email_message


def _subject_or_none(token_data: TokenData) -> uuid.UUID | None:
    """Return the token's subject as a UUID, or ``None`` when it is not one.

    Only called after a signature has been verified, but a malformed ``sub``
    must never turn a logout into a 500 — audit is best-effort by design and a
    row that cannot name its user is still worth writing without one.
    """
    try:
        return uuid.UUID(token_data.subject)
    except (ValueError, AttributeError, TypeError):
        return None


_revocation_store: RevocationStore | None = None


def get_revocation_store() -> RevocationStore:
    """Return the process-wide revocation denylist."""
    global _revocation_store
    if _revocation_store is None:
        _revocation_store = RevocationStore()
    return _revocation_store
