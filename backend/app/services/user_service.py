"""User-facing business rules that are not tied to authentication.

Profile reads, profile updates and account deletion. Anything that issues or
retires a credential lives in :mod:`app.services.auth_service` instead, so that
the rules about who may change what are stated in one place.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError, UnauthorizedError
from app.core.security import verify_password
from app.models.audit import AuditEvent
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserUpdate
from app.services.audit_service import AuditService

_USERNAME_CONFLICT = "That username is already taken."
_WRONG_PASSWORD = "The password is incorrect."


class UserService:
    """Reads and updates of user profiles.

    Takes the audit sink so an account change is recorded from the service
    that performs it, rather than from whichever router happened to call it —
    a second caller would otherwise silently stop writing the row.
    """

    def __init__(self, repository: UserRepository, audit: AuditService | None = None) -> None:
        self.repository = repository
        self.audit = audit

    async def get_by_id(self, user_id: uuid.UUID) -> User:
        """Return the user or raise :class:`NotFoundError`."""
        user = await self.repository.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def get_active_by_id(self, user_id: uuid.UUID) -> User:
        """Return the user, rejecting deactivated accounts."""
        user = await self.get_by_id(user_id)
        if not user.is_active:
            raise UnauthorizedError("This account is inactive.")
        return user

    async def list_all(self) -> list[User]:
        """Return every account, oldest first.

        **Deliberately unpaginated.** The only caller is ``GET /api/v1/users/``,
        which exists to give the role → permission wiring a gate whose refusal is
        observable end to end rather than merely declared. It is a fixture for
        the permission system, not a product surface, and pagination would only
        add a second thing that could fail between an administrator and the 200
        that proves the permission map works. It is deliberately the *least*
        defensible listing in the codebase, and it is kept in one place for that
        reason: when a real admin screen needs accounts, it gets its own paged
        query rather than inheriting this one.

        No audit row: a read is not an event, and an audit trail that recorded
        every listing would drown the sign-ins and deletions it exists to explain.

        Returns:
            Every account, oldest first.
        """
        return await self.repository.list_all()

    async def update(
        self,
        user: User,
        data: UserUpdate,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> User:
        """Apply a partial profile update, guarding username uniqueness.

        **PATCH semantics: a field is written if the client named it, and left
        alone if it did not.** ``data.model_dump(exclude_unset=True)`` is what
        distinguishes the two, and it is the only way to tell them apart here —
        Pydantic collapses "absent" and "sent as null" into the same ``None``,
        and the schema's validators collapse ``""`` into it as well. The earlier
        ``if data.display_name is not None`` guard therefore could only ever set
        those fields: once written, a display name or an avatar could never be
        cleared for the life of the account, even though the settings form offers
        exactly that by telling the user to leave the field empty. Sending
        ``null`` (or ``""``) now clears it, and omitting the key leaves it be.

        The username rule is enforced twice, as registration does it: the
        pre-check rejects the common case, and the commit is guarded as well so
        that losing the race against a concurrent write produces the same 409
        instead of escaping as a driver error. The pre-check asks whether *any
        other* account holds the name, by passing ``exclude_user_id`` — so a
        caller who already has the name writes the value they already have and
        is not told it is taken, which is what a form that always resubmits the
        username field needs.

        A username sent as ``null`` is ignored rather than written: the column
        is ``NOT NULL``, and "clear my username" is not a state an account can
        be in. A client wanting a different handle sends a different handle.

        Email and password are not editable here, by design of
        :class:`~app.schemas.user.UserUpdate`: both are identity or credential
        transitions with rules of their own (verification, session revocation,
        audit rows), and folding them into a profile edit is how one of those
        rules gets forgotten.

        Args:
            user: The caller's own account.
            data: The fields to change. A field absent from the payload is left
                alone; a field present is written, including as ``None``.
            ip_address: Client address, for the audit row. Not an identity claim.
            user_agent: Client user agent, for the audit row.

        Returns:
            The updated account, or the unchanged one when nothing writable was
            sent.

        Raises:
            ConflictError: If the requested username is held by another account.
                The unique-index ``IntegrityError`` from a lost race is converted
                to this rather than escaping as a driver error.
        """
        sent = data.model_dump(exclude_unset=True)
        fields: dict[str, object] = {}
        if sent.get("username") is not None:
            # Trimmed here rather than left to the schema or to
            # ``update_fields``'s bare setattr: the value stored has to be the
            # value the uniqueness lookup compares against.
            username = str(sent["username"]).strip()
            if await self.repository.exists_by_username(username, exclude_user_id=user.id):
                raise ConflictError(_USERNAME_CONFLICT)
            fields["username"] = username
        for key in ("display_name", "avatar_url"):
            # ``in sent``, not ``is not None``: presence is the instruction, and
            # ``None`` is a legitimate value for exactly these two columns.
            if key in sent:
                fields[key] = sent[key]
        if not fields:
            return user
        try:
            updated = await self.repository.update_fields(user, **fields)
        except IntegrityError as exc:
            # Lost the race against a concurrent update claiming this username.
            raise ConflictError(_USERNAME_CONFLICT) from exc
        if self.audit is not None:
            # Field NAMES only. A profile edit that carried its values into the
            # audit row would put a new email address or username into a log
            # that outlives the account.
            await self.audit.record(
                AuditEvent.ACCOUNT_UPDATED,
                user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"fields": sorted(fields)},
            )
        return updated

    async def delete_account(
        self,
        *,
        user: User,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Delete an account after verifying the caller's password.

        The password is required even though the caller is already
        authenticated. A password change is the account's undo, and so should
        deletion be: a stolen session token must not be enough to destroy
        everything the owner has in the product. Every other device's sessions
        go with the account, because the sessions rows cascade from it.

        Args:
            user: The account to delete.
            password: The password in force now.
            ip_address: Client address, for the audit row. Not an identity claim.
            user_agent: Client user agent, for the audit row.

        Raises:
            UnauthorizedError: If the password does not verify.
        """
        # Off the event loop. The hash is a deliberate ~200 ms of bcrypt, and a
        # synchronous verify inside `async def` blocks every other request this
        # worker is serving for the duration of one user's typo.
        if not await asyncio.to_thread(verify_password, password, user.hashed_password):
            raise UnauthorizedError(_WRONG_PASSWORD)
        if self.audit is not None:
            # Written BEFORE the row goes. The audit table keeps a SET NULL
            # foreign key precisely so the record survives the account, and it
            # can only name the account while the id still resolves.
            await self.audit.record(
                AuditEvent.ACCOUNT_DELETED,
                user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"username": user.username},
            )
        await self.repository.delete(user)
