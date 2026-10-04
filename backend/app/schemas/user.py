"""User and authentication request/response models.

Validation lives here so that malformed payloads are rejected before they reach
the service layer. ``hashed_password`` is deliberately absent from every model
that can leave the process.

Phase 2 renamed ``full_name`` to ``display_name``, added a ``username`` handle,
and replaced the ``is_superuser`` flag with a ``role``. The flag is gone from
:class:`UserRead` on purpose: the role is now the single answer to "what may
this account do?", and publishing both invites a client to branch on the one
that is no longer consulted. The column itself stays for the cases that still
set it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.config import get_settings
from app.core.permissions import permissions_for

__all__ = [
    "AVATAR_URL_SCHEMES",
    "EMAIL_PATTERN",
    "MAX_AVATAR_URL_LENGTH",
    "MAX_EMAIL_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "MAX_USERNAME_LENGTH",
    "PASSWORD_RULES",
    "USERNAME_PATTERN",
    "EmailAddress",
    "Password",
    "PasswordRule",
    "TokenPair",
    "TokenRefresh",
    "UserCreate",
    "UserDeletion",
    "UserLogin",
    "UserRead",
    "UserUpdate",
    "Username",
    "password_min_length",
    "password_rule_status",
    "validate_password_strength",
]

#: The longest password this schema accepts, counted in **UTF-8 bytes**.
#:
#: bcrypt hashes at most 72 bytes of input and silently ignores everything past
#: them, so a longer password is interchangeable with its own first 72 bytes:
#: appending "whatever you like" to a rejected password would still sign in. The
#: unit therefore has to be the one bcrypt truncates in — characters, which is
#: what a 128-character cap used to measure, is a different quantity entirely
#: for anything non-ASCII. 72 is chosen because it is bcrypt's own limit: not
#: one byte of an accepted password is discarded, and no tighter rule is imposed
#: on the ASCII passwords the policy is otherwise written for.
MAX_PASSWORD_LENGTH = 72
MAX_EMAIL_LENGTH = 320
#: Must match ``app.models.user._MAX_USERNAME_LENGTH``.
MAX_USERNAME_LENGTH = 32
#: Long enough for a provider-hosted image plus its query string; matches the
#: column width in ``app.models.user``.
MAX_AVATAR_URL_LENGTH = 2048

#: Deliberately a shape check rather than ``EmailStr``: full RFC 5322 cannot be
#: validated by a regex, and the backend does not ship email-validator for this
#: one field. Phase 1 does not verify delivery either.
EMAIL_PATTERN = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$"

#: A username must begin with a letter or a digit, because a handle rendered as
#: ``@_ada`` is awkward to read and easy to impersonate with a lookalike glyph;
#: the rest may also contain ``_`` and ``-``. The length bounds are expressed by
#: the quantifier as well as by ``Username``'s ``Field`` so the pattern is
#: self-contained for callers that reuse it directly.
USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$"

#: Only real web URLs may be stored as an avatar. Anything else — most
#: importantly ``javascript:`` — would turn a stored avatar into a script
#: injection the moment a client renders it into an ``<img src>`` or a
#: background-image.
AVATAR_URL_SCHEMES = ("http", "https")

EmailAddress = Annotated[
    str,
    Field(max_length=MAX_EMAIL_LENGTH, pattern=EMAIL_PATTERN, examples=["ada@nexus.dev"]),
]

Username = Annotated[
    str,
    Field(
        min_length=3,
        max_length=MAX_USERNAME_LENGTH,
        pattern=USERNAME_PATTERN,
        examples=["ada"],
    ),
]


# -- Password policy --------------------------------------------------------

#: A "special" character is anything that is neither a letter nor a digit, so a
#: space counts. Enumerating a fixed punctuation set would be the textbook rule
#: and the wrong one: it rejects good passphrases and good non-ASCII symbols
#: while adding nothing an attacker does not already consider. The property
#: worth enforcing is "this is not one word from a dictionary", and a separator
#: satisfies that just as well as ``!`` does.
_MIN_LENGTH_RULE = "min_length"
_UPPERCASE_RULE = "uppercase"
_LOWERCASE_RULE = "lowercase"
_DIGIT_RULE = "digit"
_SPECIAL_RULE = "special"


@dataclass(frozen=True, slots=True)
class PasswordRule:
    """One requirement in the password policy.

    ``id`` and ``label`` are the stable vocabulary the frontend renders as its
    live checklist, and ``description`` is the human-readable requirement.
    Neither carries the minimum length as a literal: it is a deployment setting,
    so :func:`password_rule_status` resolves it lazily and reports it through
    ``satisfied`` rather than baking one deployment's value into shared text.
    """

    id: str
    label: str
    description: str


PASSWORD_RULES: list[PasswordRule] = [
    PasswordRule(
        id=_MIN_LENGTH_RULE,
        label="Minimum length",
        description="Meet the minimum length configured for this deployment.",
    ),
    PasswordRule(
        id=_UPPERCASE_RULE,
        label="Uppercase letter",
        description="Include at least one uppercase letter.",
    ),
    PasswordRule(
        id=_LOWERCASE_RULE,
        label="Lowercase letter",
        description="Include at least one lowercase letter.",
    ),
    PasswordRule(
        id=_DIGIT_RULE,
        label="Digit",
        description="Include at least one digit.",
    ),
    PasswordRule(
        id=_SPECIAL_RULE,
        label="Special character",
        description="Include at least one character that is not a letter or a digit.",
    ),
]


def password_min_length() -> int:
    """Return the configured minimum password length.

    Resolved on every call rather than captured at import: the value is a
    deployment setting, and reading it once at module import would freeze
    whatever the environment happened to hold then — which is also what would
    stop a test from exercising a stricter policy.
    """
    return get_settings().password_min_length


def _rule_results(password: str) -> list[tuple[PasswordRule, str, bool]]:
    """Evaluate every policy rule against ``password``.

    Returns ``(rule, criterion, satisfied)``, where ``criterion`` is the rule
    spelled out with this deployment's actual setting — "at least 12 characters"
    rather than "the configured minimum" — so a rejection message can state the
    number the user has to reach.

    The single place the policy is expressed, so :func:`validate_password_strength`
    and :func:`password_rule_status` cannot drift apart: one is the gate, the
    other is the explanation of the same gate.
    """
    minimum = password_min_length()
    return [
        (_rule(_MIN_LENGTH_RULE), f"at least {minimum} characters", len(password) >= minimum),
        (
            _rule(_UPPERCASE_RULE),
            "an uppercase letter",
            any(char.isupper() for char in password),
        ),
        (
            _rule(_LOWERCASE_RULE),
            "a lowercase letter",
            any(char.islower() for char in password),
        ),
        (_rule(_DIGIT_RULE), "a digit", any(char.isdigit() for char in password)),
        (
            _rule(_SPECIAL_RULE),
            "a character that is not a letter or a digit",
            any(not (char.isalpha() or char.isdigit()) for char in password),
        ),
    ]


def _rule(rule_id: str) -> PasswordRule:
    for rule in PASSWORD_RULES:
        if rule.id == rule_id:
            return rule
    raise KeyError(rule_id)  # pragma: no cover - guards the module constant


def validate_password_strength(value: str) -> str:
    """Enforce the password policy and return ``value`` unchanged.

    Used by every schema that accepts a NEW password — registration, password
    change and password reset — so the same rules apply no matter which door a
    password comes through. It is attached to :data:`Password` itself rather than
    repeated per field, so a schema cannot forget it.

    Raises:
        ValueError: If the password is shorter than
            ``settings.password_min_length``, longer than
            :data:`MAX_PASSWORD_LENGTH` bytes once encoded, or is missing an
            uppercase letter, a lowercase letter, a digit or a special
            character.
    """
    if len(value.encode("utf-8")) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Must be at most {MAX_PASSWORD_LENGTH} bytes.")
    for rule, criterion, satisfied in _rule_results(value):
        if not satisfied:
            raise ValueError(f"Password must contain {criterion} ({rule.label}).")
    return value


def password_rule_status(password: str) -> list[dict[str, object]]:
    """Report, per policy rule, whether ``password`` satisfies it.

    Exists so the backend and the frontend agree on *which* rules there are and
    in what order, instead of each keeping its own list and drifting. The
    frontend renders the returned ``satisfied`` flags live as the user types.

    Args:
        password: The candidate password. Evaluated only, never stored.

    Returns:
        One ``{"id", "label", "description", "satisfied"}`` entry per entry of
        :data:`PASSWORD_RULES`, in that order.
    """
    return [
        {
            "id": rule.id,
            "label": rule.label,
            "description": rule.description,
            "satisfied": satisfied,
        }
        for rule, _criterion, satisfied in _rule_results(password)
    ]


#: A password that may be *stored*. The minimum length is deliberately absent
#: from the ``Field``: it is a setting, not a constant, and duplicating it here
#: would let the advertised schema and the enforced rule disagree. The
#: ``max_length`` is the byte budget read as a character count, which is a sound
#: over-approximation of it — a shorter string can still be longer in bytes, and
#: that is what the validator below catches.
Password = Annotated[
    str,
    Field(
        max_length=MAX_PASSWORD_LENGTH,
        examples=["Correct-Horse-Battery-7"],
    ),
    AfterValidator(validate_password_strength),
]


class _EmailNormaliser:
    """Shared before-validator collapsing emails to their canonical form."""

    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _lower_email(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


class _NameNormaliser:
    """Shared before-validator trimming free-text handles.

    Stripped but deliberately NOT lower-cased. Emails are case-insensitive, so
    collapsing them loses nothing and makes the unique index match a lookup;
    usernames are shown back to their owner and the database preserves the
    casing they registered with, so folding it here would make a user unable to
    sign in with the handle they were shown.

    A blank string becomes ``None`` so a client that clears the field can send
    ``""`` and mean "unset" rather than storing whitespace.
    """

    @field_validator("display_name", "username", mode="before", check_fields=False)
    @classmethod
    def _clean_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() or None
        return value


class UserRead(BaseModel):
    """Public representation of a user account.

    ``permissions`` is computed from ``role`` rather than stored, so a client
    branches on the capability it actually wants ("may I open the admin
    screen?") and never has to re-implement the role → permission map.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailAddress
    username: str
    display_name: str | None
    avatar_url: str | None
    role: str
    permissions: list[str] = Field(
        default_factory=list,
        description="Capabilities granted by ``role``; the client branches on these.",
    )
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None

    @model_validator(mode="after")
    def _derive_permissions(self) -> UserRead:
        # Sorted for a stable response body: ``permissions_for`` returns a
        # frozenset, whose iteration order varies with Python's per-process hash
        # seed, and a list that reorders between responses defeats client-side
        # diffing and makes the payload impossible to snapshot in a test.
        # An unknown role yields an empty list, which is the fail-closed answer
        # and matches what the permission checks themselves would decide.
        self.permissions = sorted(str(permission) for permission in permissions_for(self.role))
        return self


class UserCreate(_EmailNormaliser, _NameNormaliser, BaseModel):
    """Registration payload."""

    username: Username
    email: EmailAddress
    password: Password
    display_name: str | None = Field(default=None, max_length=255)


class UserUpdate(_NameNormaliser, BaseModel):
    """Partial update of a user's own profile.

    Deliberately cannot change ``email`` or ``password``. A password change
    revokes sessions and writes an audit row, so it has its own endpoint and its
    own schema; letting it ride along here would mean a routine profile edit
    could silently rotate credentials. ``email`` is absent for the same class
    of reason: it is an identity, and changing one has to be a verified
    transition rather than a field edit.

    ``extra="forbid"`` is what turns that from a silent no-op into an answer.
    Pydantic's default is ``"ignore"``, under which a client sending
    ``{"email": "attacker@nexus.dev"}`` gets a cheerful 200 with the field
    dropped — and a caller who reads that as "my address changed" has believed
    something false about their own identity, which is the exact outcome a
    forbid-listing exists to prevent. Rejecting names the offending field in a
    422 instead, so the client is told the route does not own it.

    The read schemas deliberately do **not** do this:
    :class:`UserRead` is validated from ORM attributes and from response
    serialisation, where an unexpected key is this layer's own business rather
    than a client's, and forbidding there would turn every added column into a
    500.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=MAX_AVATAR_URL_LENGTH)
    username: Username | None = None

    @field_validator("avatar_url", mode="before", check_fields=False)
    @classmethod
    def _check_avatar_url(cls, value: Any) -> Any:
        """Accept only ``http(s)`` URLs, and treat ``""`` as "clear the field"."""
        if not isinstance(value, str):
            return value
        candidate = value.strip()
        if not candidate:
            return None
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in AVATAR_URL_SCHEMES or not parsed.netloc:
            raise ValueError("avatar_url must be an absolute http or https URL.")
        return candidate


class UserLogin(_EmailNormaliser, BaseModel):
    """Credential payload for the token endpoints."""

    email: EmailAddress
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class TokenPair(BaseModel):
    """Access + refresh token issued by login and refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")
    # The session the tokens belong to, so the client can tell "this device"
    # apart from the others in the sessions list without a second request.
    # Optional only so a token minted outside the session flow still validates;
    # every Phase 2 issuance path sets it, and ``None`` means "unknown", not
    # "any device".
    session_id: UUID | None = None


class TokenRefresh(BaseModel):
    """Refresh payload."""

    refresh_token: str


class UserDeletion(BaseModel):
    """Confirmation payload for deleting an account.

    ``password`` re-authenticates the requester: a bearer token left in a shared
    browser's local storage is enough to delete someone's account with, and the
    account is their data, so possession of a token is not sufficient authority
    for an irreversible action.

    ``confirm`` exists because ``password`` alone does not distinguish
    "I typed my password into the form I was shown" from "a script replayed the
    last password it saw". An explicit boolean makes the intent unambiguous in
    the payload, so a mis-clicked button — or a form submitted by accident — is
    refused rather than acted on.
    """

    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    confirm: bool = Field(
        description="Must be true. An explicit acknowledgement that the account is to be deleted.",
    )

    @field_validator("confirm")
    @classmethod
    def _require_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "Account deletion requires confirm=true to state that the deletion is intended."
            )
        return value
