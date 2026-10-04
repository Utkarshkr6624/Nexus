"""Refresh-token sessions: the rows behind every device sign-in.

One row is one device. Rotation rewrites that row rather than adding another,
which is what keeps "sign out everywhere" a single UPDATE and what lets the
sessions screen show a device as "signed out on ..." rather than as gone. The
model docstring on :class:`app.models.session.Session` states the rule; this
module is where it is enforced.

The repository owns every query. The surface relied on here is::

    SessionRepository.create(*, user_id, token_hash, user_agent, ip_address,
                             expires_at) -> Session
    SessionRepository.get_by_id(session_id) -> Session | None
    SessionRepository.get_by_id_for_user(session_id, user_id) -> Session | None
    SessionRepository.list_for_user(user_id, *, include_inactive=False) -> list[Session]
    SessionRepository.list_live_ordered_by_created(user_id) -> list[Session]
    SessionRepository.touch(db_session, *, last_used_at) -> Session
    SessionRepository.rotate_token(db_session, *, token_hash, expires_at) -> Session
    SessionRepository.rotate_token_if_current(db_session, *, current_token_hash,
                                              token_hash, expires_at) -> bool
    SessionRepository.revoke(db_session, *, revoked_at) -> Session
    SessionRepository.revoke_all_for_user(user_id, *, revoked_at, except_id=None) -> int
    SessionRepository.purge_expired(*, before) -> int

The account itself is read through :class:`~app.repositories.user.UserRepository`,
built from the same request-scoped :class:`~sqlalchemy.ext.asyncio.AsyncSession`
the session repository holds, so a rotation checks the account and the row on a
single connection instead of opening a second one.

The two request-derived values written here — the client address and the user
agent — are clamped by :mod:`app.services.audit_service`, which is where the API
layer is told to read them from and which therefore owns the one bound those two
columns impose.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    token_fingerprint_matches,
)
from app.models.audit import AuditEvent
from app.models.session import Session
from app.models.user import User
from app.repositories.session import SessionRepository
from app.repositories.user import UserRepository
from app.schemas.user import TokenPair
from app.services.audit_service import AuditService, truncate_ip_address, truncate_user_agent

__all__ = ["SessionService"]

logger = logging.getLogger("app.services.session_service")

_INVALID_SESSION = "This session is no longer valid."


class SessionService:
    """Issues, rotates, lists and revokes device sessions."""

    def __init__(
        self,
        repository: SessionRepository,
        settings: Settings | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or get_settings()
        # Optional so the session lifecycle can be exercised without an audit
        # sink; production wires one so revocations leave a trail.
        self.audit = audit

    # -- Issuance ------------------------------------------------------------

    async def issue(
        self,
        *,
        user: User,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[Session, TokenPair]:
        """Mint a session row and the token pair that belongs to it.

        The refresh token carries the row's id in its ``sid`` claim and a fresh
        ``jti``, and the access token carries the *same* ``sid`` with its own
        ``jti`` — so the API can answer "which device presented this bearer?"
        without a lookup table, and so a logout can revoke exactly one device.

        The row has to exist before its id can travel in a token, and the id is
        minted by the repository at insert — ``SessionRepository.create`` does not
        take one — so the row is written once carrying the digest of a discarded
        random value and immediately rewritten with the real digest. No token
        exists between those two writes, so the transient digest cannot be
        replayed against anything; it exists only because ``token_hash`` is NOT
        NULL. If the repository is ever given an explicit ``session_id``, this
        becomes a single write and :func:`~app.core.security.new_session_id`
        moves back here.

        ``expires_at`` is the same clamp :meth:`_rotated_expiry` applies —
        the refresh token's own window against the absolute ceiling, whichever
        is shorter. A rotating token would otherwise let a session that is never
        signed out of live forever, outliving any actual sign-in event; stamping
        the absolute lifetime alone would leave it live for three weeks *after*
        the refresh token behind it is dead, which is a device the sessions
        screen keeps listing and one of the ``max_active_sessions`` slots it
        keeps occupying.

        Returns:
            The stored row and the pair handed to the client.

        Raises:
            Nothing by contract. The session cap is enforced by evicting the
            oldest live sessions, never by refusing to issue.
        """
        now = datetime.now(UTC)
        expires_at = self._initial_expiry(now)

        db_session = await self.repository.create(
            user_id=user.id,
            token_hash=hash_token(secrets.token_urlsafe(32)),
            user_agent=truncate_user_agent(user_agent),
            ip_address=truncate_ip_address(ip_address),
            expires_at=expires_at,
        )
        session_id = str(db_session.id)

        refresh_token = create_refresh_token(
            user.id,
            settings=self.settings,
            extra_claims={"sid": session_id, "jti": str(uuid.uuid4())},
        )
        db_session = await self.repository.rotate_token(
            db_session,
            token_hash=hash_token(refresh_token),
            expires_at=expires_at,
        )
        db_session = await self.repository.touch(db_session, last_used_at=now)

        pair = TokenPair(
            access_token=create_access_token(
                user.id,
                settings=self.settings,
                extra_claims={"sid": session_id, "jti": str(uuid.uuid4())},
            ),
            refresh_token=refresh_token,
            expires_in=self.settings.access_token_expire_minutes * 60,
            session_id=db_session.id,
        )
        await self._enforce_session_cap(user.id, new_session_id=db_session.id)
        return db_session, pair

    async def _enforce_session_cap(self, user_id: uuid.UUID, *, new_session_id: uuid.UUID) -> None:
        """Evict the oldest live sessions until the user is back under the cap.

        **Why this bound exists.** A refresh token is a long-lived bearer
        credential, and nothing stops an attacker who has stolen one from
        replaying it to sign in again and again. Each replay leaves another live
        session behind, so without a cap the account accumulates sessions at the
        attacker's convenience — and, just as bad, the legitimate owner gets no
        signal, because every one of those sessions looks like an ordinary
        device. Evicting the *oldest* rows means the attacker and the owner are
        treated the same way: whoever signs in too many times loses their oldest
        session first.

        The session just issued is excluded from the eviction candidates. It is
        the newest row, but ``created_at`` is a server default with one-second
        resolution, so a sign-in in the same second as another could tie, and a
        tie must never let a brand-new session evict itself.
        """
        live = await self.repository.list_live_ordered_by_created(user_id)
        excess = len(live) - self.settings.max_active_sessions
        if excess <= 0:
            return
        now = datetime.now(UTC)
        candidates = [row for row in live if row.id != new_session_id]
        for stale in candidates[:excess]:
            await self.repository.revoke(stale, revoked_at=now)

    def _initial_expiry(self, now: datetime) -> datetime:
        """Return the expiry a brand-new session row is stamped with.

        The same ``min`` :meth:`_rotated_expiry` computes, evaluated for a row
        whose ``created_at`` is ``now``: the refresh token's own lifetime
        against the absolute ceiling, whichever is shorter. Written out rather
        than delegated because the row's ``created_at`` is a server default that
        does not exist until the insert — at issue time the deadline and ``now``
        are the same instant, so taking the smaller of the two day counts is
        exactly what the rotation clamp would return.

        Args:
            now: The moment the session is being issued, application clock.

        Returns:
            The clamped, timezone-aware expiry to store on the new row.
        """
        days = min(
            self.settings.refresh_token_expire_days,
            self.settings.session_absolute_lifetime_days,
        )
        return now + timedelta(days=days)

    # -- Rotation ------------------------------------------------------------

    async def rotate(
        self,
        *,
        refresh_token: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[Session, TokenPair]:
        """Exchange a refresh token for a new pair on the same session row.

        Rotation **replaces** the row's token instead of creating a second row,
        so "one row = one device" stays true: rotating on a phone a hundred
        times still shows up as one device in the sessions screen, and signing
        out of that device still takes one UPDATE.

        The replacement is conditional, and that is what makes a token
        single-use. The row holds exactly one current digest, so two clients
        presenting the same refresh token are both asking to overwrite the same
        value; deciding the winner in SQL (``rotate_token_if_current``) is what
        stops a race between them from spending one token twice. A check-then-act
        pair cannot: under ``READ COMMITTED`` the second request still reads the
        old digest while the first one's write is uncommitted, so both believe
        they won and both new pairs stay live.

        The new expiry is clamped to the sign-in deadline rather than reset from
        it. A rotating token would otherwise slide ``expires_at`` forward on
        every refresh, which is the "absolute" lifetime in name only — a stolen
        token refreshed more often than ``refresh_token_expire_days`` would keep
        its row alive forever, and the setting is documented as a hard ceiling
        precisely so that it cannot.

        Every rejection below answers with the same message. A caller must not be
        able to tell "this session was signed out" from "this token was already
        rotated away" from "this session belongs to someone else" — the first
        tells an attacker that a guess was right. That includes losing the
        conditional update, which is reported exactly like the others.

        Args:
            refresh_token: The raw token presented by the client.
            user_agent: Updated device label from the new request.
            ip_address: Updated address from the new request.

        Returns:
            The same row, now holding the new digest, and a fresh token pair.

        Raises:
            UnauthorizedError: For any token that cannot be used.
        """
        token_data = decode_token(
            refresh_token, expected_type=TokenType.REFRESH, settings=self.settings
        )
        session_id = self._session_from_token(refresh_token, token_data.claims)
        db_session = await self._live_session_for_token(
            session_id, refresh_token, token_data.claims
        )
        user = await self._users.get_by_id(db_session.user_id)
        if user is None:
            raise UnauthorizedError(_INVALID_SESSION)
        if not user.is_active:
            raise UnauthorizedError("This account is inactive.")

        now = datetime.now(UTC)
        new_refresh = create_refresh_token(
            user.id,
            settings=self.settings,
            extra_claims={"sid": str(db_session.id), "jti": str(uuid.uuid4())},
        )
        if not await self.repository.rotate_token_if_current(
            db_session,
            current_token_hash=db_session.token_hash,
            token_hash=hash_token(new_refresh),
            expires_at=self._rotated_expiry(db_session, now),
        ):
            # Someone else rotated, revoked or expired this session between the
            # read above and this statement. Indistinguishable, on purpose, from
            # every other reason a token cannot be used.
            raise UnauthorizedError(_INVALID_SESSION)
        if db_session.user_agent != user_agent or db_session.ip_address != ip_address:
            # Refreshed from a different browser or address: the row follows the
            # device it is now being used from, which is the only way the
            # sessions screen can show something a user recognises. Written
            # after the rotation, so a rejected request leaves no trace on the
            # row it failed to rotate.
            db_session.user_agent = truncate_user_agent(user_agent)
            db_session.ip_address = truncate_ip_address(ip_address)
        db_session = await self.repository.touch(db_session, last_used_at=now)
        pair = TokenPair(
            access_token=create_access_token(
                user.id,
                settings=self.settings,
                extra_claims={"sid": str(db_session.id), "jti": str(uuid.uuid4())},
            ),
            refresh_token=new_refresh,
            expires_in=self.settings.access_token_expire_minutes * 60,
            session_id=db_session.id,
        )
        return db_session, pair

    def _rotated_expiry(self, db_session: Session, now: datetime) -> datetime:
        """Return the expiry a rotated session gets: the token's own, under the ceiling.

        Two limits apply and the smaller one wins. The token itself is good for
        ``refresh_token_expire_days`` from now, so a stolen token that nobody
        rotates is useless after that. The session is good until
        ``session_absolute_lifetime_days`` after the row was **created** — the
        sign-in, not the last refresh. Anchoring to ``created_at`` is the whole
        point: a limit measured from "now" every time a token is rotated is not
        an absolute lifetime at all, it is just the refresh window again, and an
        attacker who rotates a stolen token often enough never reaches it.

        A naive ``created_at`` is read as UTC for the same reason
        :func:`_is_expired` does: the column is timezone-aware, but a row
        assembled in memory by a caller is not guaranteed to be, and comparing
        the two would raise rather than expire anything.

        Args:
            db_session: The row being rotated, for its ``created_at``.
            now: The moment the rotation is happening, application clock.

        Returns:
            The clamped, timezone-aware expiry to store on the row.
        """
        created_at = db_session.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        deadline = created_at + timedelta(days=self.settings.session_absolute_lifetime_days)
        return min(now + timedelta(days=self.settings.refresh_token_expire_days), deadline)

    def _session_from_token(self, token: str, claims: dict[str, object]) -> uuid.UUID:
        """Resolve a refresh token's ``sid`` claim, or raise.

        The claim is required. A token minted before sessions existed carries no
        session, cannot be revoked individually, and cannot be attributed to a
        device, so accepting one silently would leave exactly the gap sessions
        were introduced to close. It is rejected instead.
        """
        raw_session_id = claims.get("sid")
        if not isinstance(raw_session_id, str) or not raw_session_id:
            raise UnauthorizedError(_INVALID_SESSION)
        try:
            return uuid.UUID(raw_session_id)
        except ValueError:
            raise UnauthorizedError(_INVALID_SESSION) from None

    async def _live_session_for_token(
        self, session_id: uuid.UUID, token: str, claims: dict[str, object]
    ) -> Session:
        """Return the session only if the token is its current, live credential.

        The expiry check here is the one place in this module that reads the
        application clock rather than the database's, and it is deliberate: the
        row is already loaded, and asking the server what time it is would be a
        second round trip on the hot path to obtain a fact the next statement
        re-asks anyway. It is an *early* rejection, not the decision — a session
        whose expiry falls inside the window between this read and the rotation
        is refused by the rotation's own ``expires_at > now()``, so the window
        is where a request gets slower, never where a lapsed session survives.
        A clock skewed ahead of the database's makes this reject a little early,
        which is the safe direction.
        """
        db_session = await self.repository.get_by_id(session_id)
        if db_session is None:
            raise UnauthorizedError(_INVALID_SESSION)
        if str(db_session.user_id) != claims.get("sub"):
            # The sid is signed, so this should be unreachable; checking anyway
            # means a future change to how the claim is built cannot turn into a
            # token that rotates somebody else's session.
            raise UnauthorizedError(_INVALID_SESSION)
        if db_session.revoked_at is not None:
            raise UnauthorizedError(_INVALID_SESSION)
        if _is_expired(db_session.expires_at, datetime.now(UTC)):
            raise UnauthorizedError(_INVALID_SESSION)
        if not token_fingerprint_matches(token, db_session.token_hash):
            # A rotated-away token: this is what makes a replayed refresh token
            # detectable, since the row only ever holds the current digest.
            raise UnauthorizedError(_INVALID_SESSION)
        return db_session

    # -- Reads ---------------------------------------------------------------

    async def list_for_user(
        self, *, user_id: uuid.UUID, current_session_id: uuid.UUID | None = None
    ) -> tuple[list[Session], uuid.UUID | None]:
        """Return the user's sessions and, separately, the current session's id.

        Only *live* sessions come back: a row whose token has expired is not a
        device that is signed in, and the response schema cannot mark the
        difference, so listing it would tell the user they are still signed in on
        hardware they abandoned months ago. Expired rows are still in the
        database — this is a change to what the listing means, not to what is
        kept.

        Marking which row is *current* is deliberately not done here: that is a
        presentation decision, and the schema layer owns it. Returning the id
        alongside the rows keeps the service from having to know how the response
        model is shaped.

        Args:
            user_id: Whose sessions to list. The scope is the caller's own.
            current_session_id: The session the request was made from, if the
                bearer carried a usable ``sid`` claim.

        Returns:
            The live rows, newest first, and the current session id as supplied.
        """
        sessions = await self.repository.list_for_user(user_id)
        return sessions, current_session_id

    # -- Revocation ----------------------------------------------------------

    async def revoke(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Session:
        """Revoke one of this user's own sessions and return the row.

        **The lookup is scoped by ``user_id`` on purpose.** Fetching by id alone
        and checking ownership afterwards would make this endpoint an IDOR: any
        authenticated user who guessed or enumerated a session id could revoke
        another user's session — and, because the answer differs for "exists but
        not yours" versus "does not exist", could use the difference to confirm
        that an id is real. Scoping in the query makes another user's session
        indistinguishable from one that never existed.

        Args:
            session_id: The session to revoke.
            user_id: The caller. Must own the session.
            ip_address: Client address, for the audit row. Not an identity claim.
            user_agent: Client user agent, for the audit row.

        Returns:
            The revoked row. Revoking an already-revoked session returns it
            unchanged rather than failing, so a double-click is not an error.

        Raises:
            NotFoundError: If the caller does not own a session with this id.
        """
        db_session = await self.repository.get_by_id_for_user(session_id, user_id)
        if db_session is None:
            raise NotFoundError("Session not found.")
        if db_session.revoked_at is not None:
            return db_session
        revoked = await self.repository.revoke(db_session, revoked_at=datetime.now(UTC))
        if self.audit is not None:
            # The session id is the subject of the event, not a secret — it is
            # what the user just acted on. The user agent is recorded because
            # "which device was this?" is the question a revocation is asked.
            await self.audit.record(
                AuditEvent.SESSION_REVOKED,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"session_id": str(session_id)},
            )
        return revoked

    async def revoke_all(
        self, *, user_id: uuid.UUID, keep_session_id: uuid.UUID | None = None
    ) -> int:
        """Revoke every live session for a user and return how many were ended.

        Args:
            user_id: Whose sessions to end.
            keep_session_id: A session to spare, for "sign out my other
                devices" where the caller stays signed in here. ``None`` ends
                them all, which is what a password reset must do.
        """
        return await self.repository.revoke_all_for_user(
            user_id,
            revoked_at=datetime.now(UTC),
            except_id=keep_session_id,
        )

    async def revoke_by_token(self, token: str) -> None:
        """Revoke the session behind ``token``, if there is one. Never raises.

        Logout has to succeed for the caller whatever the state of the world: a
        token that is already expired, already rotated away, already revoked, or
        was never a session token at all is not a reason to return an error the
        client cannot act on.

        **A bearer is not necessarily a refresh token.** A client that logs out
        with only ``Authorization: Bearer <access>`` presents a token that can
        never match ``token_hash`` — that column holds the *refresh* digest, and
        the access token is a different secret with its own ``jti``. Matching on
        the digest alone therefore finds nothing, the failure is swallowed, and
        the client walks away with a ``204`` and a session that is still live.
        When the presented token does not authenticate as the row's current
        credential, the ``sid`` claim is used instead.

        Resolving a session id out of a token is not the trust decision it looks
        like, and the reason is worth stating: ``sid`` and ``sub`` are both
        inside a **signed** JWT, so by the time they are read they are
        authenticated facts about who the token belongs to, not caller-supplied
        input. The lookup is scoped by that ``sub`` — the row must belong to the
        same user the token names — which is exactly the ownership check
        :meth:`SessionRepository.get_by_id_for_user` exists to do. Nothing here
        lets a caller name a session it does not own, because a caller cannot
        put an unsigned ``sid`` into a token this service accepts.

        Every outcome that is *not* a usable token is still swallowed, because
        logout is best-effort by design — but a database error is logged at
        WARNING rather than folded into a silent success, since "signed out"
        when the write failed is the one answer the client would act on wrongly.

        Args:
            token: The raw bearer presented on the logout request. It may be an
                access token, a refresh token, or neither.
        """
        try:
            token_data = decode_token(token, settings=self.settings)
            session_id = self._session_from_token(token, token_data.claims)
            user_id = self._user_id_from_token(token_data.claims)
        except Exception:
            return
        try:
            db_session = await self._live_session_for_token(session_id, token, token_data.claims)
        except UnauthorizedError:
            db_session = None
        except Exception:
            logger.warning("session_lookup_for_logout_failed", exc_info=True)
            db_session = None
        if db_session is None:
            try:
                db_session = await self.repository.get_by_id_for_user(session_id, user_id)
            except Exception:
                logger.warning("session_fallback_lookup_for_logout_failed", exc_info=True)
                return
        if db_session is None:
            # No session carries this id for this user: the token was never one
            # of ours, or the session is already gone. Nothing to end.
            return
        try:
            await self.repository.revoke(db_session, revoked_at=datetime.now(UTC))
        except Exception:
            logger.warning("session_revocation_for_logout_failed", exc_info=True)

    def _user_id_from_token(self, claims: dict[str, object]) -> uuid.UUID:
        """Return the ``sub`` claim as a UUID, or raise.

        Used only to *scope* a lookup, never to identify a caller. A token
        without a usable subject names no user, and there is then nothing to
        scope by, so the token is rejected rather than the lookup widened.
        """
        raw_subject = claims.get("sub")
        if not isinstance(raw_subject, str) or not raw_subject:
            raise UnauthorizedError(_INVALID_SESSION)
        try:
            return uuid.UUID(raw_subject)
        except ValueError:
            raise UnauthorizedError(_INVALID_SESSION) from None

    async def purge_expired_sessions(self, *, before: datetime | None = None) -> int:
        """Delete the session rows whose token expired before a cutoff, and return how many.

        **Nothing else prunes this table.** One row is written per sign-in and
        is only ever revoked, never deleted, so without a sweep the table grows
        by every login the product ever sees, and the index behind every
        "this user's sessions" query gets slower for every user on top of it.

        This is meant to be called once at startup, from the application
        lifespan, right after the engine is ready::

            deleted = await session_service.purge_expired_sessions()

        It is safe to run against a live database: the delete is a single
        set-based statement over rows that no longer authenticate anything.

        The cutoff is a *retention* decision, not a liveness one, which is why it
        is the one value in this module taken from the application clock rather
        than from the database — a skewed clock moves the retention boundary by
        the skew and can never make a live session look dead, because the rows
        this can select are already past ``expires_at``.

        Args:
            before: Delete rows whose ``expires_at`` is before this instant.
                Defaults to now, i.e. drop every lapsed row.

        Returns:
            The number of rows deleted.
        """
        return await self.repository.purge_expired(before=before or datetime.now(UTC))

    # -- Collaborators -------------------------------------------------------

    @property
    def _users(self) -> UserRepository:
        """The account repository, bound to the same request-scoped session.

        Built on demand from the session repository's own
        :class:`~sqlalchemy.ext.asyncio.AsyncSession` rather than injected, so
        that the "one session, one service" construction the API layer uses
        stays intact and a rotation reads the row and the account from a single
        connection.
        """
        return UserRepository(self.repository.session)


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    """Whether a session's token expiry has passed.

    A naive value is read as UTC: the column is timezone-aware, but a value
    assembled in memory by a caller is not guaranteed to be, and treating it as
    local time would expire sessions early for anyone east of UTC.
    """
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now
