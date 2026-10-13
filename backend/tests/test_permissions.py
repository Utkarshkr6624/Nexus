"""The permission vocabulary, the role → permission map, and its fail-closed edges.

Everything here is pure: no database, no HTTP. That is deliberate. The map
decides who may do what, and a test that needs a live PostgreSQL to check a
``frozenset`` would only ever run on the machine where somebody remembered to
start it.
"""

from __future__ import annotations

import pytest

from app.core.permissions import (
    ADMIN_ROLE,
    ROLE_PERMISSIONS,
    USER_ROLE,
    Permission,
    has_all_permissions,
    has_any_permission,
    has_permission,
    permissions_for,
    validate_permission,
)

#: Every permission, which is also what ``admin`` is granted.
ALL = set(Permission)

#: Values that are not permissions. Each has to deny, not raise, on the
#: request path — but must raise where a caller asks to be told it was wrong.
JUNK_PERMISSIONS = ("", "users.read.extra", "USERS.READ", "users.read ", "admin", None, 42)


# -- The vocabulary ----------------------------------------------------------


def test_every_permission_is_a_namespaced_lowercase_string():
    """The value is a persisted and published contract, so its shape is fixed."""
    for permission in Permission:
        value = str(permission)
        assert value == value.lower()
        assert value.split(".") and all(part.isidentifier() for part in value.split("."))


def test_the_permission_set_is_exactly_the_known_capabilities():
    """A new capability is a deliberate act, not a side effect of a new route.

    Phase 4 added ``calendar.read`` / ``calendar.write``; the set is asserted in
    full so the next phase cannot add one silently.
    """
    assert {permission.value for permission in Permission} == {
        "users.read",
        "users.write",
        "calendar.read",
        "calendar.write",
        "knowledge.read",
        "knowledge.write",
        "projects.read",
        "projects.write",
        "tasks.read",
        "tasks.write",
        "analytics.read",
        "jarvis.use",
    }


def test_a_permission_compares_equal_to_its_string():
    """``StrEnum`` so a role read out of the database works without unwrapping."""
    assert Permission.USERS_READ == "users.read"
    assert hash(Permission.USERS_READ) == hash("users.read")
    assert {Permission.USERS_READ: "x"}["users.read"] == "x"


# -- The map -----------------------------------------------------------------


def test_the_map_covers_exactly_the_two_known_roles():
    assert set(ROLE_PERMISSIONS) == {USER_ROLE, "admin"}
    assert USER_ROLE == "user" and ADMIN_ROLE == "admin"


@pytest.mark.parametrize("role", [USER_ROLE, ADMIN_ROLE])
def test_every_grant_is_a_real_permission(role):
    assert permissions_for(role) <= ALL


def test_a_user_may_read_and_write_only_its_own_domain():
    """A ``user`` cannot administer; that is the whole point of the ``admin`` role."""
    assert permissions_for(USER_ROLE) == frozenset(
        {
            Permission.USERS_READ,
            Permission.USERS_WRITE,
            Permission.CALENDAR_READ,
            Permission.CALENDAR_WRITE,
            Permission.KNOWLEDGE_READ,
            Permission.KNOWLEDGE_WRITE,
            Permission.PROJECTS_READ,
            Permission.PROJECTS_WRITE,
            Permission.TASKS_READ,
            Permission.TASKS_WRITE,
            Permission.ANALYTICS_READ,
            Permission.JARVIS_USE,
        }
    )
    assert Permission.USERS_READ in permissions_for(USER_ROLE)
    assert permissions_for(USER_ROLE) == permissions_for(ADMIN_ROLE)


def test_an_admin_holds_every_permission_the_build_defines():
    assert permissions_for(ADMIN_ROLE) == frozenset(ALL)


def test_the_map_values_are_frozen_sets():
    """A mutable grant would let one request widen another request's authority."""
    for role, granted in ROLE_PERMISSIONS.items():
        assert isinstance(granted, frozenset), role


# -- Fail-closed: unknown role ----------------------------------------------


@pytest.mark.parametrize(
    "role",
    ["", "User", "USER", "admin ", "superuser", "root", "none", None, 0, object()],
)
def test_an_unknown_role_is_granted_nothing(role):
    """The security-critical default.

    A role value that drifted out of step with this module is a *data* problem.
    Returning an empty set denies the request and lets the caller log it;
    raising would turn it into a 500 on every protected endpoint for every user,
    which is a worse outcome than refusing access.
    """
    assert permissions_for(role) == frozenset()


def test_an_unknown_role_denies_every_permission():
    assert not has_permission("superuser", Permission.USERS_READ)
    assert has_any_permission("superuser", Permission.USERS_READ, Permission.USERS_WRITE) is False
    assert has_all_permissions("superuser") is True  # vacuously — no requirement
    assert has_all_permissions("superuser", Permission.USERS_READ) is False


def test_permissions_for_returns_a_fresh_set_each_time():
    """Two callers must not be able to share (and widen) one grant object."""
    assert permissions_for(USER_ROLE) == permissions_for(USER_ROLE)


# -- Fail-closed: unknown permission ----------------------------------------


@pytest.mark.parametrize("permission", JUNK_PERMISSIONS)
def test_an_unknown_permission_is_never_held_even_by_an_admin(permission):
    """``admin`` is granted everything *defined*, not everything imaginable.

    Returning ``False`` rather than raising keeps a typo in a call site from
    becoming a 500; the surrounding route still denies the request.
    """
    assert has_permission(ADMIN_ROLE, permission) is False


def test_a_known_permission_is_held_by_admin_for_both_string_and_enum_forms():
    assert has_permission(ADMIN_ROLE, Permission.TASKS_WRITE) is True
    assert has_permission(ADMIN_ROLE, "tasks.write") is True


# -- any / all ---------------------------------------------------------------


def test_any_of_nothing_is_false_and_all_of_nothing_is_true():
    """The documented empty-argument semantics, stated once for both functions.

    An empty requirement is not a satisfied one (``any``), and requiring
    nothing is trivially satisfied (``all``) — which is what lets a caller build
    a permission list dynamically without special-casing the empty case.
    """
    for role in (USER_ROLE, ADMIN_ROLE, "unknown"):
        assert has_any_permission(role) is False
        assert has_all_permissions(role) is True


def test_any_is_true_when_one_grant_matches():
    assert has_any_permission(USER_ROLE, "nope", Permission.ANALYTICS_READ, "also-nope") is True
    assert has_any_permission(USER_ROLE, "nope", "also-nope") is False


def test_all_is_false_when_one_grant_is_missing():
    assert has_all_permissions(ADMIN_ROLE, Permission.USERS_READ, Permission.TASKS_READ) is True
    assert has_all_permissions(USER_ROLE, Permission.USERS_READ, "nope") is False
    # The same list under `any` still succeeds — the two must not be confused.
    assert has_any_permission(USER_ROLE, Permission.USERS_READ, "nope") is True


def test_all_and_any_agree_on_junk_in_the_list():
    """A junk entry can never satisfy ``all`` and never breaks ``any``."""
    for permission in JUNK_PERMISSIONS:
        assert has_all_permissions(ADMIN_ROLE, Permission.USERS_READ, permission) is False
        assert has_any_permission(ADMIN_ROLE, permission) is False


# -- validate_permission -----------------------------------------------------


def test_validate_permission_returns_the_member_for_a_known_value():
    assert validate_permission("users.read") is Permission.USERS_READ
    assert validate_permission(Permission.USERS_READ) is Permission.USERS_READ


@pytest.mark.parametrize("permission", JUNK_PERMISSIONS)
def test_validate_permission_raises_on_junk(permission):
    """The loud counterpart to :func:`has_permission`, for boundary callers.

    An import or config file that names a permission that does not exist should
    fail at load time, not be stored as a value no filter can match.
    """
    with pytest.raises(ValueError, match="Unknown permission"):
        validate_permission(permission)


def test_validate_permission_names_the_offending_value():
    with pytest.raises(ValueError, match=r"users\.nope"):
        validate_permission("users.nope")
