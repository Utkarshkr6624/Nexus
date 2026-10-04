"""Request-scoped context: correlation ids, timing, access logging and throttling.

:func:`add_request_context_middleware` installs :class:`RequestContextMiddleware`
as the outermost middleware, ahead of ``ServerErrorMiddleware``. A middleware
registered the ordinary way always sits *inside* that layer, and the layer is
what turns an unhandled exception into the 500 response — so a middleware
installed there never observes the failure response and cannot stamp
``X-Request-ID`` on it. The correlation id would then be missing from the
header of every 500 while still being present in the error body, defeating the
frontend's error correlation on exactly the requests that need it.

Both the header and the access line therefore work on raw ASGI messages: the
header is stamped onto ``http.response.start`` as it goes out, and the status
code is read back from the same message.

:class:`RateLimitMiddleware` sits the other way round: one layer *below* the
request context, so a throttled response is still correlated and still logged.

:func:`add_cors_middleware` uses the same mechanism for the same reason. CORS
placed the ordinary way sits inside ``ServerErrorMiddleware``, which means the
500 it renders for an unhandled exception leaves without the CORS headers, and a
browser reads that as a failed request rather than as a 500.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings, get_settings
from app.core.exceptions import ErrorCode, build_error_response
from app.core.logging import (
    REDACTED_KEYS,
    get_logger,
    log_event,
    redact,
    request_id_var,
    set_request_id,
)

__all__ = [
    "REQUEST_ID_HEADER",
    "BodyCaptureMiddleware",
    "RateLimitMiddleware",
    "RequestContextMiddleware",
    "add_cors_middleware",
    "add_request_context_middleware",
]

REQUEST_ID_HEADER = "X-Request-ID"

logger = get_logger("app.core.middleware")

#: Inbound header names that identify an upstream trace, most specific first.
_INBOUND_REQUEST_ID_HEADERS = ("x-request-id", "x-correlation-id", "x-trace-id")

#: An inbound correlation id is attacker-controlled, so it is length-capped to
#: keep log lines bounded and free of newline-forged entries.
_MAX_REQUEST_ID_LENGTH = 128

_MAX_LOGGED_BODY_CHARS = 2000

#: Upper bound on the body bytes this middleware buffers, for the replay and for
#: the access-log preview alike. Reading stops once a body crosses it — one
#: chunk may carry it past, so the buffer is the cap plus at most that chunk —
#: and the remainder of the stream is left for the handler to read. The copy
#: kept for the log is truncated to the cap on top of that.
#: :class:`BodyCaptureMiddleware` sits on unauthenticated routes such as
#: ``POST /auth/login``, so a buffer with no ceiling is one a stranger can fill.
_MAX_CAPTURED_BODY_BYTES = 256 * 1024

#: Mirrors the marker used by :func:`app.core.logging.redact` for a redacted
#: value, so a scrubbed query string reads the same as a scrubbed payload.
_REDACTED = "***redacted***"

#: ``key=value`` pairs in a raw query string whose key folds onto
#: :data:`REDACTED_KEYS`. The value runs to the next parameter separator, so
#: nothing after the secret is swallowed and nothing inside it survives.
_REDACTED_PARAM_PATTERN = re.compile(rf"(?i)\b({'|'.join(sorted(REDACTED_KEYS))})(\s*=\s*)[^&#]*")

#: Routes measured against the tighter credential budget, relative to
#: ``Settings.api_v1_prefix`` so the prefix stays a configuration concern. These
#: are the two endpoints that answer an unauthenticated caller with something an
#: attacker wants: whether a password is guessable, and whether an address has
#: an account at all. They share **one** budget rather than one each — guessing
#: a password and enumerating an address are the same attack, so a caller must
#: not be handed double the allowance by splitting its traffic across them.
_CREDENTIAL_ROUTES = frozenset({"/auth/login", "/auth/password/forgot"})

#: The bucket key every credential route is counted under, in place of its own
#: path. See :data:`_CREDENTIAL_ROUTES`.
_CREDENTIAL_BUCKET = "credential"

#: Routes measured against the same tighter budget but counted **separately** from
#: it. ``/auth/register`` is unauthenticated, answers with a 409 for an address
#: or username that already exists, and runs a bcrypt hash on every accepted
#: request — so leaving it on the generous budget made it both an account
#: enumeration oracle and a way to buy a quarter of a second of server CPU per
#: call from an unauthenticated caller.
#:
#: It does not *share* the ``credential`` bucket, because it is a different
#: attack and the sharing above has a specific justification: guessing a password
#: and enumerating an address are the same probe, so splitting traffic between
#: them must not double the allowance. Registering is not that probe, and a
#: shared bucket would mean the ordinary path through a first session — register,
#: then sign in — cannot be completed inside one window, which is a product
#: failure rather than a security win.
_ACCOUNT_ROUTES = frozenset({"/auth/register"})

#: The bucket key for :data:`_ACCOUNT_ROUTES`, in place of its own path.
_ACCOUNT_BUCKET = "account"

#: Never counted against a budget. A CORS preflight carries no credentials and
#: reaches no handler, so counting it would silently halve the attempts a
#: browser client is allowed against ``/auth/login``.
_EXEMPT_METHODS = frozenset({"OPTIONS"})

#: Bucket shared by requests whose client address is unknown, because the
#: transport reported none. Sharing one bucket is the conservative reading: a
#: caller we cannot address is a caller we cannot throttle separately.
_UNKNOWN_CLIENT = "unknown"


class RequestContextMiddleware:
    """Bind a request id, time the request and emit one access log line.

    Pure ASGI, and deliberately so: the ``X-Request-ID`` header is written onto
    the outgoing response-start message rather than onto a response object, so
    it is present whatever produced that response — including the catch-all 500,
    which ``ServerErrorMiddleware`` renders below this layer.
    """

    def __init__(self, app: ASGIApp, settings: Settings | None = None) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self.log_request_body = self.settings.log_request_body
        self.slow_request_ms = self.settings.slow_request_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bind the request id, time the call, and log exactly one line."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        request_id = _resolve_request_id(request)
        token = set_request_id(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        # No response started yet means the request failed on its way to one.
        status_code = 500

        async def send_stamped(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_stamped)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            fields: dict[str, Any] = {
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "client_ip": _client_ip(request),
                "query": _redact_query(request.url.query) or None,
                "user_agent": request.headers.get("user-agent"),
            }
            if self.log_request_body:
                fields["body"] = _body_preview(
                    getattr(request.state, "body", None),
                    total_length=getattr(request.state, "body_length", None),
                )
                fields["content_type"] = request.headers.get("content-type")
            log_event(
                logger,
                _access_log_level(status_code, duration_ms, self.slow_request_ms),
                "request_completed",
                **fields,
            )
            request_id_var.reset(token)


class BodyCaptureMiddleware:
    """Buffer the request body so it stays replayable and can be logged.

    Only installed when ``LOG_REQUEST_BODY`` is on. Recorded ASGI messages are
    replayed verbatim downstream, so route handlers see an unchanged body; the
    recording stops at :data:`_MAX_CAPTURED_BODY_BYTES`, and everything still on
    the wire is left there for the handler to read for itself. Nothing is
    truncated on the wire; what is capped is what this middleware holds.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Buffer an HTTP body, publish it on ``scope['state']``, then replay."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        messages: list[Message] = []
        captured = b""
        total_length = 0
        more_body = True
        while more_body:
            message = await receive()
            messages.append(message)
            chunk = message.get("body", b"")
            total_length += len(chunk)
            if len(captured) < _MAX_CAPTURED_BODY_BYTES:
                captured += chunk[: _MAX_CAPTURED_BODY_BYTES - len(captured)]
            more_body = message.get("more_body", False)
            if total_length > _MAX_CAPTURED_BODY_BYTES:
                # Stop reading rather than stop recording: the cap bounds what
                # is held, and a body this size that kept arriving would be
                # held in full to keep counting it. Whatever has not arrived is
                # still on the wire, and `replay` below hands the handler the
                # stream itself. `total_length` is therefore the bytes read
                # here — the whole body whenever it arrived in one piece.
                break

        state = scope.setdefault("state", {})
        state["body"] = captured
        # The size, so the log can say how much was dropped without ever
        # keeping the bytes that were.
        state["body_length"] = total_length

        pending = list(messages)

        async def replay() -> Message:
            if pending:
                return pending.pop(0)
            # Past the cap, or past the end of the body: the handler reads what
            # is left from the server itself.
            return await receive()

        await self.app(scope, replay, send)


@dataclass(frozen=True, slots=True)
class _Verdict:
    """What the limiter decided about one request."""

    allowed: bool
    #: Whole seconds the caller should wait, always >= 1 when denied. RFC 9110
    #: allows a delay to be rounded up, and rounding down would invite a caller
    #: straight back into the 429 it was just told to avoid.
    retry_after: int


class _Window:
    """One client's request count against one route, and when it started."""

    __slots__ = ("count", "started_at")

    def __init__(self, count: int, started_at: float) -> None:
        self.count = count
        self.started_at = started_at


class _FixedWindowLimiter:
    """Counters keyed by ``(client address, route)``, each over a fixed window.

    Bounded in two independent ways, because a limiter that can be made to grow
    without limit is a denial-of-service tool aimed at the server:

    * **Self-evicting.** An entry is dropped once its window has elapsed. The
      sweep runs at most once per window rather than on every request, so it
      costs one pass over the keys per window instead of one per request, and a
      quiet key is gone within one window of expiring.
    * **Capped.** Inserting past ``max_entries`` evicts the oldest-inserted key
      outright. This is FIFO rather than LRU because it is O(1): at capacity the
      alternative is a scan, and a scan per request under a flood of distinct
      keys is exactly the amplification the cap exists to prevent.

    No lock: a single event loop thread runs the read-modify-write in one
    synchronous step, and the entries are plain floats and ints.

    Both arguments are floored at one. A zero from a mistyped environment
    variable would otherwise make the sweep run on every request — a full pass
    over the keys per call — or make the cap evict from an empty dict.
    """

    def __init__(self, *, window_seconds: float, max_entries: int) -> None:
        self._window = max(1.0, window_seconds)
        self._max_entries = max(1, max_entries)
        self._windows: dict[tuple[str, str], _Window] = {}
        self._last_sweep = 0.0

    def consume(self, key: tuple[str, str], now: float, limit: int) -> _Verdict:
        """Count one request against ``key`` and say whether it may proceed.

        A denied request is **not** counted. Counting it would extend the
        penalty past the window that caused it: a caller who kept hammering
        would never see the budget return, and the ``Retry-After`` it was handed
        would be a lie.
        """
        self._sweep(now)
        window = self._windows.get(key)
        if window is None or now - window.started_at >= self._window:
            if limit <= 0:
                return _Verdict(False, self._full_window())
            self._admit(key, now)
            return _Verdict(True, 0)
        if window.count >= limit:
            return _Verdict(False, max(1, math.ceil(self._window - (now - window.started_at))))
        window.count += 1
        return _Verdict(True, 0)

    def _admit(self, key: tuple[str, str], now: float) -> None:
        """Open a fresh window for ``key`` with a count of one."""
        if len(self._windows) >= self._max_entries:
            self._windows.pop(next(iter(self._windows)))
        self._windows[key] = _Window(count=1, started_at=now)

    def _sweep(self, now: float) -> None:
        """Drop every window that has elapsed, at most once per window."""
        if now - self._last_sweep < self._window:
            return
        self._last_sweep = now
        stale = [key for key, win in self._windows.items() if now - win.started_at >= self._window]
        for key in stale:
            del self._windows[key]

    def _full_window(self) -> int:
        return max(1, math.ceil(self._window))


class RateLimitMiddleware:
    """Refuse a caller who is asking for the same route too often.

    Why it exists: ``POST /auth/login`` and ``POST /auth/password/forgot`` are
    unauthenticated and were entirely unthrottled. bcrypt answers that the
    password check costs ~250 ms, which slows an online guess down without
    stopping it, and ``/auth/password/forgot`` has nothing at all in its way.

    **What it keys on.** A client address and a route path, so a busy address
    cannot spend the budget of a different one and one route's traffic cannot
    starve another's. ``/auth/login`` and ``/auth/password/forgot`` are the
    exception: they are counted under one shared key rather than their own, so
    neither can double the allowance by splitting across the other. Both draw on
    a separate, tighter budget than the rest of the API.

    **Why a wrong password still reads as a wrong password.** The counter is
    checked *before* the request is counted, so the first ``limit`` attempts are
    answered by the real handler — 401 for a bad password, on every one of them.
    Only attempt ``limit + 1`` is refused. A limiter that answered 429 to the
    first attempt would be worse than no limiter: the attacker learns the limit
    instead of the password, and a legitimate user who mistypes is told the
    wrong thing about why.

    **Why the refusal reveals nothing.** The 429 body and the 401 body are both
    independent of whether an account exists — the limiter runs before the
    handler has looked at a single row, so a caller cannot tell "no such
    account" from "wrong password" from "you are not allowed to ask again this
    minute", and the message says only how long to wait.

    **What it does not do.** This is an in-process counter, so it is *per
    worker*: run ``uvicorn --workers 4`` or two replicas behind a load balancer
    and each holds its own tally, multiplying every limit above by the worker
    count, and restarting the process clears them. That is the same documented
    weakness the session revocation denylist in ``app.services.auth_service``
    carries and the same reason it is stated there rather than glossed over.
    A shared limit needs a shared store — Redis, or a table — which Phase 1 has
    no deployment story for yet. Until then this is a floor, not a ceiling, and
    it is worth having: it raises the cost of a flood without pretending to end
    it.

    It is also keyed on the *concrete* path, so an enumerator varying the last
    segment (``/projects/1``, ``/projects/2``, ...) draws a fresh budget each
    time. That is acceptable here because this bucket is the generous one; the
    two tight budgets it exists for are fixed paths by construction.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings | None = None,
        *,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self.enabled = self.settings.rate_limit_enabled
        self.prefix = self.settings.api_v1_prefix
        self.trust_forwarded = self.settings.rate_limit_trust_forwarded_for
        self.general_limit = self.settings.rate_limit_general_max_requests
        self.credential_limit = self.settings.rate_limit_credential_max_requests
        self._now = time_source
        self._limiter = _FixedWindowLimiter(
            window_seconds=float(self.settings.rate_limit_window_seconds),
            max_entries=self.settings.rate_limit_max_entries,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Count the request, and refuse it with 429 once the budget is gone."""
        if not self.enabled or scope["type"] != "http" or scope["method"] in _EXEMPT_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        bucket, limit, budget = self._budget_for(path)
        client = _client_ip(Request(scope), trust_forwarded=self.trust_forwarded) or _UNKNOWN_CLIENT
        verdict = self._limiter.consume((client, bucket), self._now(), limit)
        if verdict.allowed:
            await self.app(scope, receive, send)
            return

        log_event(
            logger,
            logging.WARNING,
            "rate_limited",
            path=path,
            client_ip=client,
            retry_after_seconds=verdict.retry_after,
            budget=budget,
        )
        # The same envelope every other failure uses, so the frontend needs no
        # special case: `request_id` comes from the request context this
        # middleware sits inside, and the header below comes from the layer
        # outside it.
        retry_after = verdict.retry_after
        response = build_error_response(
            code=ErrorCode.RATE_LIMITED,
            message=f"Too many requests from this address. Try again in {retry_after} seconds.",
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)

    def _is_credential_route(self, path: str) -> bool:
        """Whether ``path`` is one of the credential routes, prefix removed."""
        return self._strip_prefix(path) in _CREDENTIAL_ROUTES

    def _budget_for(self, path: str) -> tuple[str, int, str]:
        """The bucket key, the ceiling and the log label for ``path``.

        Three budgets, resolved here rather than at the call site so a route can
        never be counted under one ceiling while being reported under another.
        The tight ones share one number; what separates them is the *bucket*, and
        that is deliberate — see :data:`_ACCOUNT_ROUTES`.
        """
        relative = self._strip_prefix(path)
        if relative in _CREDENTIAL_ROUTES:
            return _CREDENTIAL_BUCKET, self.credential_limit, "credential"
        if relative in _ACCOUNT_ROUTES:
            return _ACCOUNT_BUCKET, self.credential_limit, "account"
        return path, self.general_limit, "general"

    def _strip_prefix(self, path: str) -> str:
        """``path`` with ``Settings.api_v1_prefix`` removed, if it carried one."""
        if self.prefix and path.startswith(self.prefix):
            return path[len(self.prefix) :] or "/"
        return path


def _access_log_level(status_code: int, duration_ms: float, slow_request_ms: int) -> int:
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400 or duration_ms >= slow_request_ms:
        return logging.WARNING
    return logging.INFO


def _resolve_request_id(request: Request) -> str:
    for header in _INBOUND_REQUEST_ID_HEADERS:
        candidate = request.headers.get(header)
        if candidate:
            cleaned = candidate.strip()[:_MAX_REQUEST_ID_LENGTH]
            if cleaned:
                return cleaned
    return str(uuid.uuid4())


def _client_ip(request: Request, *, trust_forwarded: bool = True) -> str | None:
    """The address to attribute a request to.

    ``trust_forwarded`` decides whether ``X-Forwarded-For`` is believed. The
    access log trusts it because a misattributed log line is a cosmetic problem;
    the rate limiter does not, by default, because a header any caller can set
    would let them mint a fresh budget by rotating it — a limiter keyed on
    attacker-controlled data is not a limiter.
    """
    if trust_forwarded:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Left-most entry is the originating client.
            return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _redact_query(query: str) -> str:
    """Scrub sensitive parameter values out of a raw query string.

    :func:`redact` only walks mappings and sequences, so a query string reaches
    the log untouched without this. The path is structural and is never
    redacted; only the values are.
    """
    return _REDACTED_PARAM_PATTERN.sub(rf"\1\2{_REDACTED}", query)


def _body_preview(body: bytes | None, *, total_length: int | None = None) -> str:
    """Render a request body for the access log, with secrets redacted.

    Only a JSON object or array is rendered, because :func:`redact` can walk
    those and can be relied on to catch the credential fields. Every other wire
    format — form encoded, multipart, malformed JSON — carries secrets in
    shapes no key-based scrub can be trusted to recognise, so those are
    summarised by size instead of quoted.
    """
    if not body:
        return ""
    size = total_length if total_length is not None else len(body)

    parsed: Any = None
    with contextlib.suppress(ValueError, TypeError):
        parsed = json.loads(body)
    if not isinstance(parsed, (dict, list)):
        reason = "truncated at the capture limit" if size > len(body) else "not a JSON object"
        return f"<not logged: {reason}, {size} bytes>"

    text = json.dumps(redact(parsed), ensure_ascii=False)
    if len(text) > _MAX_LOGGED_BODY_CHARS:
        text = f"{text[:_MAX_LOGGED_BODY_CHARS]}...<truncated>"
    return text


def _install_outermost(app: FastAPI, middleware_class: type, **kwargs: Any) -> None:
    """Wrap ``app``'s whole middleware stack in ``middleware_class``.

    ``add_middleware`` can only insert *inside* ``ServerErrorMiddleware``, which
    is the layer that renders an unhandled exception into the 500 response.
    Overriding the stack builder puts the new layer above it, and defers the
    build to the first request so exception handlers and routes registered
    after the call are still part of the stack.
    """
    build_middleware_stack = app.build_middleware_stack

    def build_with_context() -> ASGIApp:
        return middleware_class(build_middleware_stack(), **kwargs)

    app.build_middleware_stack = build_with_context  # type: ignore[method-assign]


def add_cors_middleware(app: FastAPI, settings: Settings | None = None) -> None:
    """Install CORS on ``app``, above ``ServerErrorMiddleware``.

    Installed the ordinary way it would sit *inside* that layer — and that layer
    is what turns an unhandled exception into the 500 response, so the one
    response a browser most needs to read would leave without its CORS headers.
    The frontend would report ``TypeError: Failed to fetch`` and could not tell a
    server fault from a dropped connection, which is exactly the distinction the
    500 exists to make.

    Call this **before** :func:`add_request_context_middleware`, which installs
    the outermost layer of all: each call wraps the previous one, so the order
    of the calls is the order the layers end up in.
    """
    settings = settings or get_settings()
    if not settings.cors_origin_list:
        return
    _install_outermost(
        app,
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )


def add_request_context_middleware(app: FastAPI, settings: Settings | None = None) -> None:
    """Install request context (and optional body capture) on ``app``."""
    settings = settings or get_settings()
    if settings.log_request_body:
        app.add_middleware(BodyCaptureMiddleware)
    # Added last, so it still wraps body capture, CORS and the router.
    _install_outermost(app, RequestContextMiddleware, settings=settings)
