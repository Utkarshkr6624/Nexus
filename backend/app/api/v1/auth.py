"""Authentication endpoints.

Routers stay thin: they bind HTTP to the service layer and pick the response
status. Every business rule and every error raised lives in
``app.services.auth_service`` or ``app.services.session_service``.

Request-derived values (client address, user agent) are collected once by
:func:`app.api.deps.get_client_context` and handed to the services, so the
routers never reach into the request to gather audit context themselves.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import (
    AuthenticatedUser,
    AuthService,
    AuthServiceDep,
    ClientContext,
    Credentials,
    CurrentSessionId,
    SessionService,
    SessionServiceDep,
)
from app.models.user import User
from app.schemas.security import (
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequested,
)
from app.schemas.session import SessionListRead, SessionRead
from app.schemas.user import TokenPair, TokenRefresh, UserCreate, UserLogin, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
)
async def register(
    payload: UserCreate,
    auth: AuthServiceDep,
) -> User:
    """Create an account and return it without any credential material.

    Errors: 409 when the email or username is already registered.
    """
    return await auth.register(payload)


@router.post("/login", response_model=TokenPair, summary="Exchange credentials for tokens")
async def login(
    payload: UserLogin,
    auth: AuthServiceDep,
    client: ClientContext,
) -> TokenPair:
    """Verify credentials, open a device session and issue an access/refresh pair.

    The session row is created by the service before the pair is returned, so
    the tokens the client stores are already revocable one device at a time.

    Errors: 401 for any credential failure, including an inactive account.
    """
    ip_address, user_agent = client
    return await auth.login(payload, ip_address=ip_address, user_agent=user_agent)


@router.post("/refresh", response_model=TokenPair, summary="Rotate a refresh token")
async def refresh(
    payload: TokenRefresh,
    auth: AuthServiceDep,
    client: ClientContext,
) -> TokenPair:
    """Exchange a refresh token for a new pair, invalidating the old one.

    Rotation keeps the same session row, so the device is still the same device
    after a hundred rotations — only the credential behind it is replaced.

    Errors: 401 for a malformed, expired, already-rotated or revoked token.
    """
    ip_address, user_agent = client
    return await auth.rotate(payload, ip_address=ip_address, user_agent=user_agent)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke the supplied refresh token",
)
async def logout(
    auth: AuthServiceDep,
    credentials: Credentials,
    payload: TokenRefresh | None = None,
) -> Response:
    """Revoke the supplied token, if any, and return no content.

    Both the optional body and the optional bearer are honoured: a client that
    sends a refresh token in the body is signing out that device, and a client
    that sends nothing but a bearer is signing out of the session it is calling
    from. Always 204 — a client that cannot log out is a client that stays
    signed in.

    A client that sends the same token in both places is revoking one session,
    so it is revoked once: calling through twice would write a second
    ``user_logout`` row for a single sign-out, and the audit trail would
    over-count every logout the frontend performs this way.

    **A body token that names a different session wins, and the bearer is left
    alone.** Revoking an unusable body token used to fall through to the bearer
    branch as well, so ``POST /auth/logout {"refresh_token": "<stale>"}`` ended
    the caller's own live session and answered 204 — a client retrying a logout
    with a token it had already spent silently signed itself out of the session
    it meant to keep, and was told it had succeeded. :meth:`AuthService.revoke`
    ignores what it cannot decode, so the body token now revokes the session it
    names — if it names one that still exists — and nothing else happens.
    """
    body_token = payload.refresh_token if payload is not None else None
    bearer_token = credentials.credentials if credentials is not None else None
    if body_token is not None and body_token != bearer_token:
        await auth.revoke(body_token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if bearer_token is not None:
        await auth.revoke(bearer_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke every other session",
)
async def logout_all(
    current_user: AuthenticatedUser,
    session_id: CurrentSessionId,
    auth: AuthServiceDep,
) -> Response:
    """Sign out every *other* device and keep the caller signed in here.

    The caller's own session is spared by its ``sid`` claim. Without that the
    endpoint would end the session it was called from, and the client would have
    to sign in again immediately after asking to sign out elsewhere.
    """
    await auth.logout_all(user_id=current_user.id, keep_session_id=session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserRead, summary="Current authenticated user")
async def me(current_user: AuthenticatedUser) -> User:
    """Return the account the bearer token belongs to."""
    return current_user


@router.get("/sessions", response_model=SessionListRead, summary="List the caller's sessions")
async def sessions(
    current_user: AuthenticatedUser,
    session_id: CurrentSessionId,
    session_service: SessionServiceDep,
) -> SessionListRead:
    """List the caller's device sign-ins, marking the one they are calling from.

    ``sid`` is read from the access token rather than from the database, so the
    "current" marker is a property of this request and not something that has to
    be stored or kept up to date. Turning that into the per-row ``is_current``
    flag is presentation, which is why it happens here — on the way out through
    :meth:`SessionRead.for_request` — and not in the service.

    Errors: 401 for a missing, invalid or revoked token.
    """
    rows, current_id = await session_service.list_for_user(
        user_id=current_user.id, current_session_id=session_id
    )
    return SessionListRead(
        sessions=[SessionRead.for_request(row, current_session_id=current_id) for row in rows],
        current_id=current_id,
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke one of the caller's sessions",
    responses={
        204: {
            "headers": {
                "Warning": {
                    "description": "Present when the revoked session is the caller's own — see below."
                }
            }
        }
    },
)
async def revoke_session(
    session_id: UUID,
    current_user: AuthenticatedUser,
    session_service: SessionServiceDep,
    client: ClientContext,
    session_id_of_request: CurrentSessionId,
) -> Response:
    """Revoke one of the caller's own sessions.

    Ownership is enforced by scoping the lookup to the caller, and a session
    belonging to somebody else therefore answers **404, never 403**. That is not
    a cosmetic choice: a 403 would confirm the id exists, turning this endpoint
    into a probe for which session ids are real, whereas a 404 is what an id
    that never existed also returns.

    **Revoking the session you are calling from is allowed, and says so.** It is
    the same row the ``Revoke`` button on the Sessions screen removes when the
    dialog names "this is the device you are using right now", and the caller is
    entitled to end it — but a bare 204 is indistinguishable from revoking a
    phone, and the very next request with that token is a 401. A ``Warning``
    header on that one answer closes the gap without changing the status code a
    client that legitimately wants to sign itself out is already handling.
    ``POST /auth/logout`` is the route whose *only* job is that, and it is
    unchanged.

    Errors: 404 when the caller does not own a session with this id.
    """
    ip, agent = client
    await session_service.revoke(
        session_id=session_id,
        user_id=current_user.id,
        ip_address=ip,
        user_agent=agent,
    )
    headers = (
        {
            "Warning": '199 - "This request revoked the session it was made with; '
            'further requests on this credential will be refused."'
        }
        if session_id_of_request is not None and session_id == session_id_of_request
        else None
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=headers)


@router.patch(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Change the caller's password",
)
async def change_password(
    payload: PasswordChange,
    current_user: AuthenticatedUser,
    session_id: CurrentSessionId,
    auth: AuthServiceDep,
) -> Response:
    """Set a new password and end every session except the caller's own.

    The caller stays signed in on this device: their session is identified by the
    ``sid`` claim and exempted from the revocation. Every *other* session is
    ended, because a password change is only half a control unless the sessions
    signed in with the old one are closed too.

    Errors: 401 when the current password is wrong, 409 when the new one matches
    the current one.
    """
    await auth.change_password(
        user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        keep_session_id=session_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password/forgot",
    response_model=PasswordResetRequested,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a password reset link",
)
async def forgot_password(
    payload: PasswordResetRequest,
    auth: AuthServiceDep,
    client: ClientContext,
) -> PasswordResetRequested:
    """Accept a reset request for an address.

    The response is identical whether or not the address is registered, so this
    endpoint cannot be used to discover which addresses have accounts. 202 rather
    than 204 because the "work" is asynchronous by nature: the acknowledgement
    is a promise that a link is on its way, not a claim that it is.

    ``dev_token`` carries the raw reset token where there is no mail transport
    to send it through, and only where ``dev_expose_reset_token`` says so — it
    is ``None`` everywhere else, production included.

    Errors: 422 for a malformed address only. There is no 404 for an unknown one.
    """
    ip_address, user_agent = client
    dev_token = await auth.request_password_reset(
        email=str(payload.email), ip_address=ip_address, user_agent=user_agent
    )
    return PasswordResetRequested(dev_token=dev_token)


@router.post(
    "/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Redeem a password reset token",
)
async def reset_password(
    payload: PasswordResetConfirm,
    auth: AuthServiceDep,
    client: ClientContext,
) -> Response:
    """Set a new password from a reset token and end every session.

    Every session goes, including any that existed when the reset was
    requested: this is the recovery path for a compromised account, so leaving
    one behind would leave the compromise in place behind a new password.

    Errors: 401 for a token that is unknown, spent, expired or not a reset
    token — all four answer identically, so a spent link cannot be told from a
    guessed one.
    """
    ip_address, user_agent = client
    await auth.complete_password_reset(
        token=payload.token,
        new_password=payload.new_password,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["AuthService", "SessionService", "router"]
