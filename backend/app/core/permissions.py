"""Role-based authorisation: the vocabulary and the role → permission map.

Design decision
---------------
Permissions live in a Python map, not in a ``roles``/``permissions`` join table.
The role set is tiny and fixed — ``user`` and ``admin`` — and it is chosen in
this file, not by an operator at runtime. A table would buy runtime mutability
we have no use for and would cost a query on every protected request, plus a
class of bug where the database says one thing and the code assumes another.
When the role set stops being fixed (per-tenant roles, custom roles, delegated
scopes) this map is the only thing that has to change: the call sites already
ask for a :class:`Permission`, never for a role.

Every lookup here is fail-closed. :func:`permissions_for` returns an empty set
for an unrecognised role instead of raising, because a role value that drifted
out of step with this module is a *data* problem, and raising on every
protected endpoint would turn it into a 500 for every user. Doing nothing is
the correct degraded behaviour: the request is denied, the incident is visible,
and nobody's working session breaks.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

__all__ = [
    "ADMIN_ROLE",
    "ROLE_PERMISSIONS",
    "USER_ROLE",
    "Permission",
    "has_all_permissions",
    "has_any_permission",
    "has_permission",
    "permissions_for",
    "validate_permission",
]

#: Role values are duplicated from ``app.models.user.UserRole`` as plain strings.
#: ``app.core`` must stay importable without pulling in the ORM layer — and the
#: models import the declarative base, so importing them here would make every
#: consumer of this module (including Alembic's env) pay for a full model import.
#: If ``UserRole`` ever gains a value, add it here in the same change.
USER_ROLE = "user"
ADMIN_ROLE = "admin"


class Permission(StrEnum):
    """A single capability a role may hold.

    A :class:`~enum.StrEnum` so each member's value is the stable string that
    ends up in code, in audit rows and in API responses. Declaring them
    explicitly (rather than deriving from route names) keeps the grant
    deliberate: adding an endpoint does not silently grant it to anyone.
    """

    USERS_READ = "users.read"
    USERS_WRITE = "users.write"
    PROJECTS_READ = "projects.read"
    PROJECTS_WRITE = "projects.write"
    TASKS_READ = "tasks.read"
    TASKS_WRITE = "tasks.write"
    ANALYTICS_READ = "analytics.read"
    # The calendar is the one Phase 4 surface with its own pair rather than
    # reusing `tasks.*`. It is separate because a calendar row is not a task row:
    # a meeting has no task, a deadline does have one, and conflating them would
    # make "who may book time" and "who may edit the work" the same question.
    CALENDAR_READ = "calendar.read"
    CALENDAR_WRITE = "calendar.write"
    # Phase 5 knowledge has its own pair rather than reusing `projects.*`. Every
    # one of those tables is per-user and filtered by `owner_id` in the query, so
    # the permission is not what stops a cross-user read — it is a separate,
    # separately-revocable capability for the knowledge surface, which is the
    # one part of NEXUS a user might reasonably want to be able to turn off
    # without losing their planner.
    KNOWLEDGE_READ = "knowledge.read"
    KNOWLEDGE_WRITE = "knowledge.write"
    # Phase 7: talking to the Jarvis language model. Separate from every other
    # grant so the conversational surface can be withdrawn on its own. It is not
    # a licence for the tools Jarvis calls: each NEXUS tool re-enters the
    # ordinary routes with the caller's own token and needs that route's grant.
    JARVIS_USE = "jarvis.use"


def validate_permission(value: Permission | str) -> Permission:
    """Coerce a stored or user-supplied value into a :class:`Permission`.

    Raises:
        ValueError: If the value is not a known permission. Callers that accept
            permissions from outside the code (an import, a config file) need
            this to reject typos loudly at the boundary rather than silently
            granting nothing.
    """
    if isinstance(value, Permission):
        return value
    try:
        return Permission(value)
    except ValueError:
        raise ValueError(f"Unknown permission: {value!r}") from None


#: What each role may do. Read-only access is the baseline for every role;
#: writes are an explicit grant, never an accident of ordering.
#:
#: ``user`` holds ``users.write`` because every account must be able to edit
#: its own profile and change its own password. That grant is scoped by
#: ownership, not by the permission: the router compares the target id against
#: the authenticated subject and rejects anything else. Withholding the
#: permission instead would not be safer, only invisible — a self-service
#: profile edit would need a bespoke exception path that bypasses this map
#: anyway, and the one thing the map is for (a readable answer to "what may an
#: ordinary user do?") would become "everything except the thing they most
#: obviously need".
#:
#: Phase 4 grants ``user`` BOTH calendar permissions. The alternative — a
#: read-only calendar for ordinary users — was considered and rejected on the
#: same grounds as ``users.write`` above: a personal planner whose owner cannot
#: book a meeting is not a planner, and the planner is the feature. Withholding
#: the grant would not be safer, only broken, because the calendar is *per user*
#: and every write is ownership-scoped in the query rather than in this map: a
#: ``calendar.write`` holder can only ever touch rows carrying the caller's own
#: ``owner_id``. The permission that would matter here is trust in that filter,
#: which every other write in the system already extends.
#:
#: Phase 5 grants ``user`` BOTH knowledge permissions for exactly the same
#: reason. Notes, concepts, resources, bookmarks, documents, categories and
#: knowledge links are all per-user, and every read in
#: :mod:`app.services.knowledge_service` carries ``owner_id`` in its ``WHERE``
#: clause — including the polymorphic link endpoints, which have no foreign key
#: and are therefore resolved through an owner-scoped lookup *before* an edge is
#: written. The grant is a statement about the feature existing, not a statement
#: that this map enforces the isolation.
#:
#: **``tests/test_permissions.py`` asserts both the full member set and the full
#: ``user`` grant as literal sets, so it must be extended with these two
#: members or it will fail.** Flagged here because a red test is otherwise easy
#: to misread as a regression in this file.
ROLE_PERMISSIONS: Mapping[str, frozenset[Permission]] = {
    USER_ROLE: frozenset(
        {
            Permission.USERS_READ,
            Permission.USERS_WRITE,
            Permission.PROJECTS_READ,
            Permission.PROJECTS_WRITE,
            Permission.TASKS_READ,
            Permission.TASKS_WRITE,
            Permission.ANALYTICS_READ,
            Permission.CALENDAR_READ,
            Permission.CALENDAR_WRITE,
            Permission.KNOWLEDGE_READ,
            Permission.KNOWLEDGE_WRITE,
            Permission.JARVIS_USE,
        }
    ),
    # ADMIN_ROLE is `frozenset(Permission)`, so both new members are granted to
    # admin automatically — adding a permission can never silently omit it.
    ADMIN_ROLE: frozenset(Permission),
}


def permissions_for(role: str) -> frozenset[Permission]:
    """Return the permissions granted to ``role``.

    An unrecognised role yields an empty set rather than an error. This is the
    fail-closed default and it is deliberate: this function runs on the request
    path, so raising here would convert a mismatched role value into a 500 on
    every protected endpoint, for every user, until someone noticed a data
    problem. An empty set denies the request and lets the caller log it.
    """
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: str, permission: Permission | str) -> bool:
    """Whether ``role`` holds ``permission``.

    An unrecognised permission is treated as not held rather than raising, for
    the same reason :func:`permissions_for` does not raise on a bad role.
    """
    granted = permissions_for(role)
    if not granted:
        return False
    try:
        return validate_permission(permission) in granted
    except ValueError:
        return False


def has_any_permission(role: str, *permissions: Permission | str) -> bool:
    """Whether ``role`` holds at least one of ``permissions``.

    With no permissions supplied this is ``False``: an empty requirement is not
    a satisfied one.
    """
    return any(has_permission(role, permission) for permission in permissions)


def has_all_permissions(role: str, *permissions: Permission | str) -> bool:
    """Whether ``role`` holds every one of ``permissions``.

    With no permissions supplied this is ``True``: requiring nothing is trivially
    satisfied, which keeps callers that build the requirement list dynamically
    from having to special-case the empty case.
    """
    return all(has_permission(role, permission) for permission in permissions)
