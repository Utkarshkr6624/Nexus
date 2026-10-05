"""Domain exceptions and the shared API error envelope.

Every non-2xx response produced by NEXUS has exactly this body::

    {"error": {"code": "...", "message": "...", "details": {...}|null,
               "request_id": "uuid"}}

``code`` is a stable snake_case contract for the frontend; ``message`` is
written to be shown to a user and therefore never carries a stack trace, SQL or
a secret. Details are machine-only and carry structured validation info. For a
5xx the originating exception's own text is logged and never echoed, because
that text is attacker-influenced in some handlers and by definition outside our
control.

``details`` has exactly one documented shape
--------------------------------------------
Call sites raise errors with whatever structured context they have — a
``{"field": ..., "accepted": [...]}`` for a refused enum, an
``{"allowed": [...], "from": ..., "to": ...}`` for a state machine, a raw
``{"max_depth": 1}`` for a nesting rule. Those keys are *extras*: they are what
a client needs to recover, and they stay on the wire untouched.

The one shape every renderer is entitled to rely on is ``errors``, a list of
``{"field", "message"}`` entries. It used to be one of three competing
conventions, so a renderer that understood ``errors[]`` — the documented one, and
the one this module's own 422 handler emits — showed nothing at all for the two
others, which is the worst outcome a validation message can have: a refusal the
caller cannot see is a refusal they will retry identically. :func:`_details`
therefore guarantees ``errors`` on every non-empty ``details``, synthesising the
single entry from whatever the raise site supplied, and leaves every original
key exactly where it was.

The synthesis is deliberately conservative. It never invents a ``field`` — a
details mapping that names no input produces an entry with a ``message`` alone,
so a client that keys form errors by field name keeps treating it as a banner
error rather than silently attaching the sentence to an input that does not
exist. It also never reorders or rewrites a key, so the extras keep working for
the clients that already read them.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

__all__ = [
    "DETAILS_ERRORS_KEY",
    "ConflictError",
    "ErrorCode",
    "ForbiddenError",
    "NexusError",
    "NotFoundError",
    "UnauthorizedError",
    "ValidationError",
    "build_error_response",
    "error_payload",
    "install_exception_handlers",
    "resolve_request_id",
]

logger = logging.getLogger("app.core.exceptions")

#: The only message a client may ever see for a server-side failure.
_INTERNAL_ERROR_MESSAGE = "An internal server error occurred."


class ErrorCode:
    """Stable machine-readable error codes exposed by the API."""

    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
    BAD_REQUEST = "bad_request"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    #: Phase 11. Raised when ML classification was asked for and the classifier
    #: is not serving — no checkpoint, no torch, or a failed load. 503 rather
    #: than 404 because nothing about the *request* is wrong: the deployment is,
    #: and a client retrying once the operator has loaded a checkpoint is
    #: behaving correctly.
    ML_UNAVAILABLE = "ml_unavailable"


class NexusError(Exception):
    """Base class for every error the API deliberately raises.

    Subclasses set ``code``, ``status_code`` and ``message``; instances may
    override the message and attach structured ``details``.
    """

    code: str = ErrorCode.INTERNAL_ERROR
    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = dict(details) if details else {}
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


class ValidationError(NexusError):
    """The request was well-formed but semantically invalid (HTTP 422)."""

    code = ErrorCode.VALIDATION_ERROR
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    default_message = "The submitted data is invalid."


class NotFoundError(NexusError):
    """The addressed resource does not exist (HTTP 404)."""

    code = ErrorCode.NOT_FOUND
    status_code = HTTPStatus.NOT_FOUND
    default_message = "The requested resource was not found."


class UnauthorizedError(NexusError):
    """Credentials are absent, invalid or expired (HTTP 401)."""

    code = ErrorCode.UNAUTHORIZED
    status_code = HTTPStatus.UNAUTHORIZED
    default_message = "Authentication is required."


class ForbiddenError(NexusError):
    """The caller is known but not permitted (HTTP 403)."""

    code = ErrorCode.FORBIDDEN
    status_code = HTTPStatus.FORBIDDEN
    default_message = "You do not have permission to perform this action."


class ConflictError(NexusError):
    """The request collides with existing state (HTTP 409)."""

    code = ErrorCode.CONFLICT
    status_code = HTTPStatus.CONFLICT
    default_message = "The request conflicts with the current state of the resource."


def resolve_request_id(request: Request) -> str:
    """Return the correlation id for ``request``.

    The contextvar is the fast path. It is already unbound by the time the
    catch-all handler runs, because Starlette's ``ServerErrorMiddleware`` sits
    outside the user middleware stack — hence the fallback to the value the
    request middleware stashed on ``request.state``.
    """
    return request_id_var.get() or getattr(request.state, "request_id", None) or ""


#: The one key every ``details`` is guaranteed to carry. A list, because an error
#: can be about several inputs at once — that is what the Pydantic handler's
#: ``exc.errors()`` is — and a client should never have to ask which of a
#: singular and a plural key it is looking at.
DETAILS_ERRORS_KEY = "errors"


def _details(details: Mapping[str, Any] | None, *, message: str) -> dict[str, Any] | None:
    """Return ``details`` in the one documented shape, extras preserved.

    ``errors`` is added when the raise site did not supply it. The entry is
    built from the details themselves rather than from a guess about which raise
    site this was: ``field`` when the details name an input, and a message taken
    from ``reason`` when the details carry one (a framework ``HTTPException``
    whose ``detail`` was not a string) and from the envelope's own sentence
    otherwise.

    A details mapping with no field yields an entry with no field, on purpose.
    The frontend's ``fieldErrorMessages`` skips entries whose ``field`` is not a
    string and ``bannerError`` therefore keeps showing the banner, which is what
    a refusal with nothing to attach to an input should do; attaching it to a
    made-up input name would hide the sentence instead.

    Args:
        details: Whatever the raise site supplied. ``None`` and ``{}`` both mean
            "no structured context", and both produce ``None`` on the wire.
        message: The envelope's own message, the fallback for a synthesised
            entry's sentence.

    Returns:
        The details to serialise, or ``None`` when there were none.
    """
    if not details:
        return None
    body = dict(details)
    if not isinstance(body.get(DETAILS_ERRORS_KEY), list):
        entry: dict[str, Any] = {}
        field = body.get("field")
        if isinstance(field, str) and field:
            entry["field"] = field
        reason = body.get("reason")
        entry["message"] = reason if isinstance(reason, str) and reason else message
        body[DETAILS_ERRORS_KEY] = [entry]
    return body


def error_payload(
    *,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the wire representation of an error."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": _details(details, message=message),
            "request_id": request_id or request_id_var.get() or "",
        }
    }


def build_error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """Serialise the shared error envelope as a :class:`JSONResponse`."""
    return JSONResponse(
        status_code=status_code,
        content=error_payload(
            code=code,
            message=message,
            details=details,
            request_id=request_id or request_id_var.get(),
        ),
        headers=dict(headers) if headers else None,
    )


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Flatten Pydantic v2 errors into JSON-safe, field-addressed dicts."""
    details: list[dict[str, Any]] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part != "body"]
        entry: dict[str, Any] = {
            "field": ".".join(location) or "body",
            "message": error.get("msg", "Invalid value"),
            "type": error.get("type", "value_error"),
        }
        context = error.get("ctx")
        if isinstance(context, Mapping):
            # ``ctx`` may carry exception objects that are not JSON serialisable.
            entry["context"] = {key: str(value) for key, value in context.items() if key != "error"}
        details.append(entry)
    return details


_STATUS_CODE_TO_ERROR_CODE: Mapping[int, str] = {
    HTTPStatus.BAD_REQUEST: ErrorCode.BAD_REQUEST,
    HTTPStatus.UNAUTHORIZED: ErrorCode.UNAUTHORIZED,
    HTTPStatus.FORBIDDEN: ErrorCode.FORBIDDEN,
    HTTPStatus.NOT_FOUND: ErrorCode.NOT_FOUND,
    HTTPStatus.CONFLICT: ErrorCode.CONFLICT,
    HTTPStatus.UNPROCESSABLE_ENTITY: ErrorCode.VALIDATION_ERROR,
    HTTPStatus.METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
    HTTPStatus.TOO_MANY_REQUESTS: ErrorCode.RATE_LIMITED,
}


def _status_code_to_code(status_code: int) -> str:
    """Map an HTTP status onto the stable error-code contract.

    The frontend branches on ``code``, so an unmapped 4xx must not be reported
    as ``internal_error``: that would blame us for a failure the caller caused
    (413, 415, 402 and friends). ``INTERNAL_ERROR`` is reserved for 5xx.
    """
    mapped = _STATUS_CODE_TO_ERROR_CODE.get(status_code)
    if mapped is not None:
        return mapped
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return ErrorCode.INTERNAL_ERROR
    return ErrorCode.BAD_REQUEST


def install_exception_handlers(app: FastAPI) -> None:
    """Register every handler that produces the shared error envelope."""

    @app.exception_handler(NexusError)
    async def _handle_nexus_error(request: Request, exc: NexusError) -> JSONResponse:
        logger.info(
            "domain_error",
            extra={
                "error_code": exc.code,
                "status_code": exc.status_code,
                "path": request.url.path,
            },
        )
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, UnauthorizedError) else None
        return build_error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            details=exc.details or None,
            headers=headers,
            request_id=resolve_request_id(request),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = _validation_details(exc)
        logger.info(
            "request_validation_failed",
            extra={"status_code": 422, "path": request.url.path},
        )
        return build_error_response(
            code=ErrorCode.VALIDATION_ERROR,
            message="The request body or query parameters failed validation.",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            details={DETAILS_ERRORS_KEY: details},
            request_id=resolve_request_id(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        code = _status_code_to_code(exc.status_code)
        if exc.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            logger.error(
                "http_exception",
                extra={
                    "status_code": exc.status_code,
                    "path": request.url.path,
                    "detail": str(detail),
                },
                exc_info=False,
            )
            # Never echo a 5xx detail. It is text we did not write — a framework
            # or dependency string, or application text about the failure — and
            # it can carry SQL, module paths or credentials. ``message`` is
            # contractually safe to render, so a 5xx answers with the same
            # generic string as the catch-all handler and leaves ``details``
            # empty; the detail stays in the log, reachable by ``request_id``.
            message = _INTERNAL_ERROR_MESSAGE
            details = None
        else:
            message = detail if isinstance(detail, str) else "Request failed."
            details = {"reason": str(detail)} if not isinstance(detail, str) else None
        headers = getattr(exc, "headers", None)
        return build_error_response(
            code=code,
            message=message,
            status_code=exc.status_code,
            details=details,
            headers=headers,
            request_id=resolve_request_id(request),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # The traceback stays server-side; the client sees a generic message.
        logger.exception(
            "unhandled_exception",
            extra={
                # This handler runs outside the request middleware, so the
                # contextvar is already unbound: state the id explicitly.
                "request_id": resolve_request_id(request),
                "path": request.url.path,
                "method": request.method,
                "exception_type": type(exc).__name__,
            },
        )
        return build_error_response(
            code=ErrorCode.INTERNAL_ERROR,
            message=_INTERNAL_ERROR_MESSAGE,
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            request_id=resolve_request_id(request),
        )
