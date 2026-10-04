"""User profile and account endpoints.

Every route here acts on the *caller*. There is no ``/users/{id}`` on purpose:
ownership is therefore implicit rather than checked, because the only subject a
request can name is the one its bearer token already resolved to.

The one exception is the administrative listing at the bottom, which exists to
give the permission system a gate that is real and observable rather than
merely declared. See its docstring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    AuthenticatedUser,
    SettingsDep,
    UserServiceDep,
    get_authenticated_user,
    get_client_context,
)
from app.core.deps import get_current_active_superuser, require_permission
from app.core.permissions import Permission
from app.models.user import User
from app.schemas.user import UserDeletion, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the caller's profile",
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
async def update_me(
    payload: UserUpdate,
    current_user: AuthenticatedUser,
    users: UserServiceDep,
    request: Request,
    settings: SettingsDep,
) -> User:
    """Update the caller's own profile.

    **Ownership is implicit here**: the route has no path parameter, so the only
    account it can touch is the one the bearer token resolved to. The
    ``USERS_WRITE`` permission is still checked, because it is the grant that
    says "accounts may edit their own profile" at all — the check answers
    *whether the capability exists*, the token answers *whose*. Removing the
    dependency would not make the route safer, only unguarded: it would mean a
    future route added next to this one had no obvious place to hang the
    permission check.

    Email and password are not editable through this route; both have their own
    endpoints because both are identity or credential transitions with rules
    (verification, session revocation, audit rows) that a profile edit has no
    business performing.

    Errors: 409 when the requested username is held by another account. A
    username the caller already holds is not a conflict — the profile form
    resubmits it on every save.
    """
    ip, agent = get_client_context(request, settings)
    return await users.update(current_user, payload, ip_address=ip, user_agent=agent)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete the caller's account",
    # No ``require_permission`` dependency, unlike the PATCH above. That is not
    # a deliberate asymmetry to read meaning into: both roles currently hold
    # USERS_WRITE, so the gate here would be a no-op that happens to pass. The
    # password re-check in the payload is what actually authorises this one, and
    # it is stronger than a role check. Add the dependency if USERS_WRITE ever
    # stops being universal.
)
async def delete_me(
    payload: UserDeletion,
    current_user: AuthenticatedUser,
    users: UserServiceDep,
    request: Request,
    settings: SettingsDep,
) -> Response:
    """Delete the caller's account permanently.

    The password is required in addition to the bearer token, and ``confirm``
    must be ``true`` in the payload. A token left in a shared browser is enough
    to *read* an account; it is not enough to destroy one.

    Sessions and outstanding password-reset tokens go with the account by
    database cascade, so every device is signed out without a second pass. The
    audit rows do **not**: ``audit_logs.user_id`` is ``ON DELETE SET NULL``, so an
    account's security history outlives the account and is still readable
    afterwards — a trail that vanished along with the thing it describes could
    not be used to investigate that deletion at all. The service records the
    ``ACCOUNT_DELETED`` row before the row goes, so the surviving record still
    names who it was.

    Errors: 401 when the password does not verify.
    """
    ip, agent = get_client_context(request, settings)
    await users.delete_account(
        user=current_user,
        password=payload.password,
        ip_address=ip,
        user_agent=agent,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/",
    response_model=list[UserRead],
    summary="List accounts (admin only)",
    dependencies=[
        Depends(require_permission(Permission.USERS_READ)),
        Depends(get_current_active_superuser),
        Depends(get_authenticated_user),
    ],
)
async def list_users(users: UserServiceDep) -> list[User]:
    """List every account — a permission-system fixture, not a product feature.

    It exists so the role → permission wiring has a route whose refusal is
    observable end to end: an ordinary caller must get 403, an administrator must
    get 200, and the difference between them must be the permission check rather
    than an accident of how the route was written. Without a route like this,
    ``ROLE_PERMISSIONS`` is a map nothing ever reads.

    Two gates, deliberately distinct. ``USERS_READ`` is the capability check and
    is what the permission map expresses; requiring the administrator role *as
    well* is the narrowing that says this listing is not part of the product's
    surface. Both must pass. It returns an unbounded list with no pagination
    because a fixture that could itself need pagination would be a worse
    fixture.

    **A third gate, and it is about revocation rather than permission.** The two
    above resolve the caller through ``app.core.deps.get_current_user``, which
    never consults the ``sessions`` table or the revocation denylist — so a token
    from a device that has since been signed out, revoked everywhere or
    superseded by a password change went on reading this listing, and every
    account's address with it, for the full ``ACCESS_TOKEN_EXPIRE_MINUTES``.
    :func:`app.api.deps.get_authenticated_user` is the dependency that does
    consult both. It says nothing about *who* may call — that is still the two
    gates above — only whether the bearer is one anybody has signed out of,
    which is why it belongs in ``dependencies=`` and goes unused as a parameter.

    Errors: 403 for any caller whose role does not satisfy both checks; 401
    when the bearer no longer has a live session behind it.
    """
    return await users.list_all()


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
