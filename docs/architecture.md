# NEXUS — Architecture

Reference for the platform: the Phase 1 technical foundation, the Phase 2 identity slice,
and the modules Phases 3 through 9 put on top of them. This document explains *how the
system is put together and why*, and defers to [`../README.md`](../README.md) for
installation, day-to-day commands and troubleshooting. Everything below was read from the
code; where a number appears it came from the repository, not from intention. Where
something could **not** be exercised here — which currently means anything needing
Docker — that is said explicitly rather than glossed: see
[What has not been run](#what-has-not-been-run).

**Scope.** Phase 1 delivered the platform skeleton and one working vertical slice
(auth + `users` table). Phase 2 grew that slice into a real account system: persistent
device sessions, a password policy, password recovery, role-based permissions, an audit
trail, and the settings surface to drive them. Phases 3 through 9 then made the module
surface real — Projects and Tasks, Planner, Knowledge, Analytics, Risks and
Recommendations, Developer, Learning and Career. Each has a router, a repository, a
service, a migration and pages that read real rows. **Search, AI Assistant and
Experiments remain placeholder pages** and have no API at all. Sections 1–16 were written
against Phase 2 and still describe that slice where the text is phase-specific;
§17 covers what the later phases added.

---

## Table of contents

| Section | Contents |
| --- | --- |
| [1. System context](#1-system-context) | Processes, ports, topology, configuration flow |
| [2. Backend layering](#2-backend-layering) | The dependency rule and what each layer may know |
| [3. Request lifecycle](#3-request-lifecycle) | What happens to a request, in order |
| [4. Error contract](#4-error-contract) | The one envelope and the code table |
| [5. Health and readiness](#5-health-and-readiness) | Liveness vs. the database probe |
| [6. Authentication and authorisation](#6-authentication-and-authorisation) | Tokens, sessions, rotation, revocation, RBAC, the password policy, password reset, the audit trail |
| [7. Persistence](#7-persistence) | Engine, pool, models, migrations, test database |
| [8. Configuration model](#8-configuration-model) | One `Settings` object, derived URLs, production guards |
| [9. Observability](#9-observability) | Logging sinks, redaction, correlation |
| [10. Event loop and platform constraints](#10-event-loop-and-platform-constraints) | The psycopg/Windows problem, solved once |
| [11. Process model](#11-process-model) | Why one worker, and what that forbids |
| [12. Frontend architecture](#12-frontend-architecture) | Providers, routing, registry, data access, build |
| [13. Testing architecture](#13-testing-architecture) | Fixtures, markers, what is and is not covered or verified |
| [14. Container topology](#14-container-topology) | Compose services and why they are wired that way |
| [15. Extension roadmap](#15-extension-roadmap) | Named seams for later phases |
| [16. Design decisions](#16-design-decisions) | Decision → rationale → cost |
| [17. Developer, Learning and Career](#17-developer-learning-and-career) | The three subsystems Phase 8 and 9 added: git scanning, and two surfaces that describe a person |
| [18. Remediation pass over Phases 1–9](#18-remediation-pass-over-phases-19) | What the audit found, what shipped, and what in this document moved because of it |
| [19. Phase 10 — ML training](#19-phase-10--ml-training) | The `backend/ml/` package: a second process with a second interpreter, two models, and one execution boundary |

---

## 1. System context

Three processes, all on one machine. The application calls no external service, has no
cloud account and no third-party API. Phase 10 adds one outbound path that is *not* on
the request path: the one-off download of the classifier's pretrained checkpoint from
Hugging Face, the first time `make ml-train-small` runs. After that the pipeline is
entirely local, it never runs inside the API process, and nothing downstream of it is
loaded by the application.

| Process | Host port | Started by | Notes |
| --- | --- | --- | --- |
| PostgreSQL 16 (`postgres:16-alpine`) | 5432 | Compose, or your own server | named volume `nexus_pgdata` |
| FastAPI (uvicorn) | 8000 | `backend/run.py` | `NEXUS_HOST` / `NEXUS_PORT` / `NEXUS_RELOAD` |
| Vite dev server | 5173 | `npm run dev` | `strictPort: true`; preview server on 4173 |

```text
  browser ──5173──► Vite dev server ──proxy /api, /health──► FastAPI ──5432──► PostgreSQL
                                                  (8000)
```

Two transport topologies coexist, and the difference matters:

| Mode | `VITE_API_BASE_URL` | What the browser calls | Where `/api/v1` resolves |
| --- | --- | --- | --- |
| Host development | `http://localhost:8000/api/v1` (from `.env.example`) | the backend cross-origin | the API, CORS applies |
| Compose | `/api/v1` (set in `docker-compose.yml`) | same-origin | Vite's proxy → `VITE_DEV_PROXY_TARGET` |

`VITE_DEV_PROXY_TARGET` defaults to `http://localhost:8000` and is set to
`http://backend:8000` by Compose, because inside the compose network
`localhost:8000` would be the frontend container itself. The `server.proxy` block
in `frontend/vite.config.ts` also covers `/health`, which sits outside the version
prefix, and `preview` is given the same routes so a previewed production bundle
behaves like the reverse proxy that will eventually front the API.

### Configuration flow

One `.env` at the repository root feeds both processes. `Settings`
(`backend/app/core/config.py`) searches `.env`, `../.env`, `../../.env`, so the
file is found regardless of the working directory. `docker-compose.yml` reads the
same file directly and injects `${VAR:-default}` values per service. There is no
second configuration channel: every value that shapes behaviour is an environment
variable listed in [`.env.example`](../.env.example).

**The `127.0.0.1` rule.** Database URLs use `127.0.0.1`, never `localhost`. On
Windows `localhost` resolves to `::1` first and psycopg's async driver is IPv4
only, so the TCP connect neither succeeds nor fails — it hangs. The application
does not rewrite the host for you (`Settings.postgres_host` defaults to
`127.0.0.1`, and that is the end of it); the helper scripts in `scripts/` do the
rewrite, because a script that hangs is useless for diagnosis.

---

## 2. Backend layering

Four layers, one dependency rule: **dependencies point downward only.**

| Layer | Package | Knows about | Must not know about |
| --- | --- | --- | --- |
| HTTP | `app/api/v1/*.py`, `app/api/deps.py` | FastAPI, schemas, services | SQL, ORM internals |
| Domain | `app/services/*.py` | schemas, repositories, `app.core` | FastAPI (never imported) |
| Data | `app/repositories/*.py` | SQLAlchemy, ORM models | HTTP, error semantics |
| Infrastructure | `app/core/*`, `app/db/*`, `app/models/*` | everything below it | services, routers |

The rule is enforced by intent and visible in the code: `app/services/auth_service.py`
states in its module docstring that it never imports FastAPI, and
`app/repositories/user.py` never raises a domain error — an `IntegrityError` is
allowed to propagate so the service can translate it into `409 conflict`.

That translation happens in the service layer on **both** write paths, which is
what makes "one translation point" true rather than aspirational:

| Path | Pre-check | Race guard |
| --- | --- | --- |
| `AuthService.register` | `exists_by_email` / `exists_by_username` → `ConflictError` | `IntegrityError` → the same `ConflictError`, chained, with the *specific* message chosen from the constraint name |
| `UserService.update` (username) | `get_by_username` → `ConflictError` | `IntegrityError` on commit → the same `ConflictError`, chained |

Without the second column, a concurrent writer would win the race and the loser
would surface a driver error as a 500. `UserService.update` normalises the handle
itself before the lookup, so the value the uniqueness check compares against is
the value the column will hold, whichever caller is writing. Username uniqueness
was added in Phase 2 alongside the column; `UserService.update` now has a router
(`app/api/v1/users.py`) and tests.

Phase 2 did not relax the rule, it repeated it: a new repository
(`SessionRepository`, `PasswordResetRepository`, `AuditRepository`) returns `None`
for a miss and lets driver errors propagate, and the service in front of it —
`SessionService`, `AuthService`, `UserService` — is what raises `NotFoundError`,
`UnauthorizedError`, `ConflictError` or `ForbiddenError`.

Two dependency providers split the HTTP wiring:

| Module | Responsibility |
| --- | --- |
| `app/core/deps.py` | Identity and authorisation: bearer scheme, current user, optional user, superuser, the `sid` claim, `require_permission()`, session → repository. Talks to the repository directly and imports no service, which keeps the graph acyclic. |
| `app/api/deps.py` | Layering on top: session → repository → service, the revocation check that turns a cryptographically valid JWT into a rejected one, and `get_client_context()` (client address + user agent, gathered once and handed to the services). Re-exports the `core` names so routers have one import site. |

`app/core/permissions.py` also lives in the infrastructure layer, and the reason
it can: it deliberately duplicates the two role strings from
`app.models.user.UserRole` as plain constants, because `app.core` must stay
importable without pulling in the ORM (Alembic's `env` imports it). The two
definitions must change in the same commit — see
[Role-based access control](#role-based-access-control).

Schemas (`app/schemas/`) sit on the HTTP boundary and are the only place Pydantic
validation runs. `hashed_password` is absent from every model that can leave the
process.

---

## 3. Request lifecycle

Registered in `backend/app/main.py`; the order below is the execution order,
outermost first.

| # | Layer | Installed by | Behaviour |
| --- | --- | --- | --- |
| 1 | `RequestContextMiddleware` | `add_request_context_middleware` → `_install_outermost` | Binds the correlation id, stamps `X-Request-ID` onto the outgoing `http.response.start`, times the request, emits the access line. |
| 2 | `BodyCaptureMiddleware` | `app.add_middleware`, added last from inside `add_request_context_middleware`, and only when `LOG_REQUEST_BODY=true` | Buffers the ASGI body messages, publishes a capped copy on `scope["state"]`, replays them verbatim. |
| 3 | `CORSMiddleware` | `app.add_middleware` | Preflight and header work. `X-Request-ID` is in `expose_headers`; credentials are allowed. |
| 4 | `RateLimitMiddleware` | `app.add_middleware`, added **first** | Fixed-window counters keyed by client address and concrete path; answers 429 with the shared envelope plus `Retry-After`. Added by the final remediation pass; skips `OPTIONS`, and treats a missing client address as one shared `unknown` bucket |
| 5 | `ServerErrorMiddleware` | Starlette, in `build_middleware_stack` | Catches anything escaping layer 6 and renders it through the catch-all handler. |
| 6 | `ExceptionMiddleware` → router → dependencies | Starlette, in `build_middleware_stack` | Registered handlers, routing, `app/api/v1/router.py` — which now mounts nineteen routers (`health`, `auth`, `users`, `projects`, `tasks`, `tags`, `activity`, `calendar`, `work_sessions`, `planner`, `availability`, `knowledge`, `analytics`, `developer`, `risks`, `recommendations`, `intelligence`, `learning`, `career`) — mounted at `settings.api_v1_prefix`. |

`add_middleware` inserts outermost-last, so the order above is the *reverse* of the order
`create_app` registers in: it adds the rate limiter first, then CORS, then body capture, and
the last one added is the outermost of the three.

`RateLimitMiddleware` sits in two deliberate places at once. It is **below**
`RequestContextMiddleware`, so a throttled response is still correlated and still
access-logged — a 429 with no `X-Request-ID` and no log line is a response an operator cannot
tie to the client that caused it. And it is **inside** `CORSMiddleware`, immediately above the
router, so a browser that is refused can read the 429 and its `Retry-After` instead of seeing
an opaque CORS failure. Being innermost is also what makes it the only layer positioned to
refuse a request before a handler runs.

`RequestContextMiddleware` is **above** `ServerErrorMiddleware`, and that is
load-bearing rather than incidental. Starlette builds the user middleware stack
*inside* `ServerErrorMiddleware`, so anything registered with `add_middleware`
sits below the layer that renders an unhandled exception into a 500 — and never
sees that response. Registered the ordinary way, the middleware could set
`X-Request-ID` on a normal response only, and every 500 would have gone out with
no correlation header while the error *body* still carried a `request_id`. That
split is worse than neither: a bug report quoting the header would name nothing,
and the frontend's error correlation would fail on precisely the requests that
need it.

The fix has two halves, both in `backend/app/core/middleware.py`:

| Mechanism | What it does |
| --- | --- |
| `_install_outermost` | Overrides the app's `build_middleware_stack` so the whole stack is wrapped by `RequestContextMiddleware`. The build is still deferred to the first request, so handlers and routes registered after the call are still part of it. |
| A pure-ASGI `__call__` | The header is written onto the `http.response.start` message with `MutableHeaders` rather than onto a response object, so it is present whatever produced that response — including a 500 rendered below it. |

Two consequences follow for the middleware itself. It is not a
`BaseHTTPMiddleware` subclass, so `request.state` and response-header work are
done by hand rather than delegated. And it recovers the status code from the
same message it stamps, defaulting to 500 when the request fails before a
response starts — so a connection that dies mid-handler still produces one
access line, at ERROR.

Inside `__call__` the correlation id is bound with `set_request_id()` before
anything downstream can log, the token is reset in the `finally` after the access
line is emitted, and `request.state.request_id` is stamped alongside it as a
fallback.

Two things about the stack below it are unchanged by any of this. Routers
translate exceptions into nothing at all — they pick a success status and return,
and every error surfaces as an exception rendered centrally. And exactly one
access line is produced per request, in the middleware's `finally`, at ERROR for
5xx, WARNING for 4xx or for a duration at or above `SLOW_REQUEST_MS`, INFO
otherwise; uvicorn's own access log is disabled in `configure_logging` so the two
cannot double-count.

### Request id

| Property | Behaviour |
| --- | --- |
| Source | `X-Request-ID`, then `X-Correlation-ID`, then `X-Trace-ID`, else a fresh UUID4 |
| Trust | Inbound values are attacker-controlled: stripped and capped at 128 characters so a log line cannot be forged with newlines |
| Propagation | Bound to a `contextvars.ContextVar`, attached to every log record by `ContextFilter`, stamped onto the response-start message, embedded in every error body |
| Catch-all path | The catch-all handler runs *inside* this middleware's call, so the contextvar is still bound and is the primary source. `resolve_request_id()` falls back to `request.state.request_id` as a safety net, not as the main path. |

Because the header and the body are produced by different layers from the same
id, they are guaranteed to agree — on **every** status, 500 included. The
`assert_error_envelope` fixture asserts that equality, and
`tests/test_error_handling.py` repeats it on the 5xx path, along with the
caller-supplied, over-long and newline-forged inbound id cases.

---

## 4. Error contract

Every non-2xx response has the same shape (`backend/app/core/exceptions.py`):

```json
{
  "error": {
    "code": "not_found",
    "message": "The requested resource was not found.",
    "details": null,
    "request_id": "0f1e..."
  }
}
```

[`api-conventions.md`](api-conventions.md#the-error-envelope) owns this contract
as a wire format and is the document to read for it. What follows is the
implementation of it inside this backend.

| Field | Contract |
| --- | --- |
| `code` | Stable snake_case. Branch on this, never on `message`. |
| `message` | Always safe to render to a user. No stack traces, SQL, driver names or internal paths. |
| `details` | Machine-only. `null` unless the error carries structured data (for `validation_error`, `{"errors": [...]}` with `field` / `message` / `type`). Always `null` on a 5xx. |
| `request_id` | Equal to the `X-Request-ID` response header on **every** status, 500 included — the header and the body are stamped from one id by the middleware above. `tests/test_error_handling.py` asserts the equality on the 5xx path; `assert_error_envelope` asserts it everywhere. |

| `code` | HTTP | Raised by |
| --- | --- | --- |
| `validation_error` | 422 | `RequestValidationError` handler, `ValidationError` |
| `bad_request` | 400, **and any 4xx with no explicit mapping** (413, 415, 402, …) | `StarletteHTTPException` mapping |
| `unauthorized` | 401 | `UnauthorizedError` (adds `WWW-Authenticate: Bearer`) |
| `forbidden` | 403 | `ForbiddenError` |
| `not_found` | 404 | `NotFoundError` |
| `method_not_allowed` | 405 | `StarletteHTTPException` mapping |
| `conflict` | 409 | `ConflictError` |
| `rate_limited` | 429 | `StarletteHTTPException` mapping (code reserved; no limiter is implemented) |
| `internal_error` | any 5xx | catch-all handler, and any `StarletteHTTPException` at 5xx — traceback and detail stay in the log |

Two rules in that table are worth stating separately, because both are deliberate
and neither is obvious from the envelope.

**A 5xx never echoes a caller-supplied `detail`.** `StarletteHTTPException` lets
a handler choose any `detail` string, and a 5xx raised that way would have put
that text on the wire. It is text the application did not author for a client —
a framework or dependency string, or application prose about the failure — and
it can carry SQL, module paths or credentials. So `_handle_http_exception`
replaces the message with the single constant `_INTERNAL_ERROR_MESSAGE`
("An internal server error occurred.") and forces `details` to `None` for any
status at or above 500; the original detail is logged as `http_exception` at
ERROR and stays reachable by `request_id`. `tests/test_error_handling.py` drives
a route that raises a 5xx carrying a constraint name and a SELECT, then asserts
the string is absent from the body and present in the log.

**`internal_error` is reserved for 5xx.** An unmapped status used to fall through
to it, which meant a 413 or a 415 — the caller's fault, entirely — was reported
as a server fault. The frontend branches on `code`, so that would send every
oversized upload and every wrong content type down the "something is broken on
our side" path. `_status_code_to_code` now returns `bad_request` for any 4xx
with no explicit mapping and reserves `internal_error` for 5xx;
`tests/test_errors.py::test_error_codes_are_stable_snake_case` asserts the
invariant `not code.startswith("internal") or status >= 500`.

The mapping from HTTP status to `code` lives in one dict
(`_STATUS_CODE_TO_ERROR_CODE`), so a handler never invents a code. Domain errors
subclass `NexusError`, which carries `code`, `status_code` and a default
message; instances may override the message and attach `details`.

The frontend consumes this envelope in `frontend/src/lib/api-client.ts`: every
failure path throws a single `ApiError` carrying `status`, `code`, `details` and
`requestId`, with a status-derived fallback for responses that are not in the
envelope shape (a proxy 502, for instance).

---

## 5. Health and readiness

Two endpoints with deliberately different contracts.

| Endpoint | Touches the database | Body | Contract |
| --- | --- | --- | --- |
| `GET /health` | No | `{"status": "ok"}` | Liveness. Must stay green while PostgreSQL is down, otherwise a dependency failure triggers a restart loop instead of a page. |
| `GET /api/v1/health` | Yes — timed `SELECT 1` | `{status, app, version, environment, database:{status, latency_ms}, uptime_seconds, timestamp}` | Readiness. Returns **200** with `status: "degraded"` and `database.status: "unavailable"` when the probe fails — the endpoint reporting degradation is itself healthy. |
| `GET /` | No | service, version, environment, `api_version`, endpoint links | Meta. |

Uptime is computed from `time.monotonic()`, so it is unaffected by wall-clock
changes. The probe also runs once at startup (`app/main.py:_lifespan`) and is
logged as `database_probe` at INFO when it succeeds, WARNING when it does not —
startup never fails because the database is down. The lifespan reads
`app.state.settings` — the object `create_app(settings=...)` was given — so a
caller that supplies its own settings gets those settings for logging and for the
probe, not the global singleton; the CORS origins, docs URLs and API prefix were
already built from it. The probe itself is additionally wrapped in a `try`, so a
future failure mode degrades the log line rather than blocking startup.

The probe's own timeout is described under [Persistence](#7-persistence); it is
the reason a wedged server produces `degraded` quickly instead of hanging.

The container healthchecks use `/health`, not the detailed endpoint, for exactly
the reason above — `docker-compose.yml`'s `backend` healthcheck and the
`HEALTHCHECK` line in `backend/Dockerfile` both curl `127.0.0.1:$NEXUS_PORT/health`.

The Dashboard health card on the frontend polls `/api/v1/health` every 30 s with
a 5 s `staleTime` (`frontend/src/features/health/use-health.ts`). In Phase 1 it
was the only live data dependency in the shell; Phase 2 added the settings
Sessions tab, which queries `/auth/sessions` on mount and after every mutation,
and the account forms, which are mutation-only. Phases 3 through 9 made it one
data dependency among many — every live module page now queries its own routes,
and the health card is the only reader of `/api/v1/health`.

---

## 6. Authentication and authorisation

### Tokens

HS256 JWTs signed with `SECRET_KEY`. Lifetimes come from
`ACCESS_TOKEN_EXPIRE_MINUTES` (default 60) and `REFRESH_TOKEN_EXPIRE_DAYS`
(default 7). `TokenPair.expires_in` is the access lifetime in seconds.

| Claim | Set by | Purpose |
| --- | --- | --- |
| `sub` | `app/core/security.py` | user id (UUID, string-encoded) |
| `type` | `app/core/security.py` | `access` or `refresh` — the claim that stops a refresh token being replayed as a bearer credential |
| `iat`, `nbf`, `exp` | `app/core/security.py` | standard time claims |
| `jti` | `app/services/auth_service.py`, `app/services/session_service.py` | token id; the key of the access-token revocation denylist |
| `sid` | `app/services/session_service.py` | **New in Phase 2.** The `sessions` row this token belongs to |

`decode_token()` requires `exp`, `sub` and `type` to be present, restricts the
accepted algorithm to `settings.jwt_algorithm`, and converts *every* failure —
bad signature, wrong algorithm, expiry, malformed input, wrong `type` — into a
single `UnauthorizedError`. Callers never have to reason about which PyJWT
exception occurred.

Passwords use bcrypt at cost 12, chosen above the library default because ~250 ms
per hash on commodity hardware is the intended trade against brute force. Input is
truncated to bcrypt's 72-byte ceiling rather than allowed to raise.

`sid` is what makes a token attributable to a device. It is an *enrichment*, never
a gate: `get_current_session_id()` returns `None` for anything it cannot parse
rather than raising, because every endpoint that needs one already resolved the
user first, and a cosmetic lookup should never produce a second, differently-worded
401 on top of the first.

### The user model

Phase 2 grew the `users` table and changed what leaves the process.

| Column | Status | Note |
| --- | --- | --- |
| `email` | unchanged | Unique; lower-cased by a `mode="before"` validator before storage and before every uniqueness check |
| `username` | **new** | 3–32 chars, must start alphanumeric, then `A-Za-z0-9_-`. Unique, and **case-preserving** — trimmed, never folded, so the handle a user is shown is the handle they can type |
| `display_name` | **renamed** from `full_name` | The product surface calls it a display name everywhere; keeping a differently-named column would force a translation at every call site |
| `avatar_url` | **new** | Optional, absolute `http`/`https` only |
| `role` | **new** | `user` / `admin` as a plain string with an application-side `UserRole`, *not* a Postgres enum: adding an enum value needs `ALTER TYPE … ADD VALUE`, which cannot run inside a transaction block on some deployment paths and is exactly the kind of migration that fails halfway through a deploy. The cost is that the database no longer rejects a misspelled role on its own |
| `password_changed_at` | **new** | Nullable and deliberately **not** defaulted to the creation time — rows predating the column have nothing truthful to backfill, and a fabricated "password set at" would log every pre-existing session out on first sign-in. `NULL` reads as "never changed since signup" |
| `last_login_at` | existed | Stamped on a successful login |
| `is_superuser` | **superseded** | The column remains and is still honoured, because Phase 1 rows can only be promoted by setting it and dropping the check would silently un-promote every pre-Phase-2 administrator. It is gone from `UserRead` — publishing both it and `role` invites a client to branch on the one that is no longer consulted |

`UserRead` also carries `permissions`, a `list[str]` **derived from `role` at
serialisation time** and sorted, so two responses diff cleanly. It is never stored.
The point is that a client branches on "may I open this screen?" and never
re-implements the role → permission map.

### Flows

| Flow | Endpoint | Rules |
| --- | --- | --- |
| Register | `POST /api/v1/auth/register` | Duplicate e-mail **or username** → `409`, checked twice: a pre-check for the common case and an `IntegrityError` catch for the concurrent-registration race, with the constraint name selecting which message comes back. The new account gets `role = "user"`. |
| Login | `POST /api/v1/auth/login` | Every failed check returns the same message *and takes comparable time*, so probing cannot distinguish "unknown email" from "wrong password" — see below. On success `last_login_at` is stamped and a device session is opened. Inactive accounts are rejected separately. |
| Refresh | `POST /api/v1/auth/refresh` | Single-use rotation: the presented token's digest is replaced on the session row before the new pair is issued, so a replay finds no match and fails with 401. The row is *kept*, so the device stays the same device. |
| Logout | `POST /api/v1/auth/logout` | Optional body and/or bearer token; revokes whatever is parseable and returns 204. Unusable tokens are ignored — logout must never fail. |
| Log out everywhere | `POST /api/v1/auth/logout-all` | Revokes every session except the caller's own, identified by `sid`. Without that exemption the endpoint would end the session it was called from. |
| Me | `GET /api/v1/auth/me` | Resolves the caller through `AuthenticatedUser`, which is the only place the revocation denylist is consulted. |
| Sessions | `GET /api/v1/auth/sessions` | The caller's live devices, with `is_current` computed by comparing each row's id against the `sid` claim. `token_hash` and `user_id` are absent from `SessionRead`. |
| Revoke one | `DELETE /api/v1/auth/sessions/{id}` | Scoped to the caller's own rows; **404, never 403**, for anyone else's. See below. |
| Change password | `PATCH /api/v1/auth/password` | Requires the current password, ends every session except the caller's, and stamps `password_changed_at`. A new password equal to the current one is a `409`. |
| Request reset | `POST /api/v1/auth/password/forgot` | Identical body for a known and an unknown address. |
| Redeem reset | `POST /api/v1/auth/password/reset` | Ends **every** session, including ones that predate the request. |
| Update profile | `PATCH /api/v1/users/me` | `display_name`, `avatar_url`, `username`. `email` and `password` are deliberately *not* editable here — both are identity/credential transitions with rules a routine profile edit has no business performing. |
| Delete account | `DELETE /api/v1/users/me` | Requires the account password **and** `confirm: true`. Sessions and reset tokens go by cascade; audit rows do not. |

`AuthenticatedUser` layers on top of `CurrentUser`: a JWT remains cryptographically
valid after logout, and `get_authenticated_user` is what makes the denylist
observable. Endpoints that need real logout semantics must depend on
`AuthenticatedUser`, not `CurrentUser`.

### Account enumeration

The shared error message hides whether an address is registered. That is only
half of it — the response *clock* leaks the same fact just as well, and a shared
string does nothing about a 1 ms answer for an unknown address against a ~250 ms
answer for a known one.

`AuthService.authenticate` therefore always runs a bcrypt verify. When the
lookup misses, it verifies the submitted password against `_decoy_hash()` — a
bcrypt hash of a random value, computed once per process by a `@cache`d helper so
the cost stays off the import path, and discarded so no submitted password can
ever match it. The result is then folded into the one shared branch:

```python
user = await self.repository.get_by_email(str(data.email))
stored_hash = user.hashed_password if user is not None else _decoy_hash()
password_ok = verify_password(data.password, stored_hash)
if user is None or not password_ok:
    raise UnauthorizedError(_INVALID_CREDENTIALS)
```

Both failures then cost one bcrypt verify plus one index lookup, and say the same
thing.

The password-reset endpoint takes the other approach, because it cannot afford the
cost: `request_password_reset` writes a row only when the address is registered,
but the *response* is byte-for-byte identical either way, so the only observable
difference is `dev_token` — which is `null` in production, precisely because it
would otherwise disclose the same fact.

### Sessions

Phase 1 had exactly one durable thing to say about a credential: the in-process
denylist. Phase 2 added a `sessions` table and made a session a first-class row.

```text
POST /auth/login ─► SessionService.issue()
                      1. insert the row (id minted by the repository)
                      2. mint the refresh token carrying sid = row.id
                      3. rotate_token() → store hash_token(refresh)
                      4. touch() → last_used_at = now
                      5. mint the access token carrying the same sid
                      6. _enforce_session_cap()
```

One row is one browser. Rotation **replaces** `token_hash` on the row rather than
inserting a second one, which is what makes "sign out everywhere" a single
`UPDATE … WHERE user_id = ?` instead of a search through a token table, and what
lets the sessions screen show a revoked device as "signed out on …" rather than as
gone.

| Column | Holds | Why it exists |
| --- | --- | --- |
| `token_hash` | SHA-256 hex of the **current** refresh token | The raw token is never persisted and never logged, so a database dump yields digests that cannot be replayed |
| `user_agent` | Raw header, verbatim | The device label the sessions UI shows; truncated to 512 at the storage boundary |
| `ip_address` | Client address, as text not `inet` | An audit aid for "where did this sign-in come from", **not** an authorisation control — a spoofed value costs nothing |
| `expires_at` | Absolute, from `SESSION_ABSOLUTE_LIFETIME_DAYS` | Compared against `func.now()` server-side, so a stale clock cannot revive a row. Independent of rotation: a rotating token would otherwise let a session that is never signed out of live forever, outliving any actual sign-in event |
| `last_used_at` | Stamped on each rotation | Drives "last active" in the sessions UI; nullable because a session that has never been refreshed has no such moment |
| `revoked_at` | `NULL` means live | The single column the repository filters and updates to revoke |

No `User.sessions` relationship is declared. A plain relationship defaults to lazy
`select` loading, which under AsyncIO raises `MissingGreenlet` the moment a caller
touches it outside an awaited query; the repositories query these rows explicitly.

**The session cap.** `MAX_ACTIVE_SESSIONS` (20) does not refuse a sign-in. It
evicts the *oldest* live rows until the account is back under the cap, excluding the
session just created. A refresh token is a long-lived bearer credential, and nothing
stops an attacker who has stolen one from replaying it to sign in again and again;
each replay leaves another live session behind, so without a bound the account
accumulates sessions at the attacker's convenience and the legitimate owner gets no
signal, because every one of them looks like an ordinary device. Evicting the oldest
means attacker and owner are treated identically. The exclusion is not cosmetic:
`created_at` is a server default with one-second resolution, so two sign-ins in the
same second can tie, and a tie must never let a brand-new session evict itself.

**Every rotation rejection answers identically.** `SessionService.rotate` collapses
"this session was signed out", "this token was already rotated away", "this session
belongs to someone else" and "this session has expired" into one
`UnauthorizedError("This session is no longer valid.")`. A caller that can tell those
apart has learned something an attacker wants.

### Role-based access control

`app/core/permissions.py` is the whole authorisation system: a `Permission` StrEnum
of **eleven** capabilities, a `ROLE_PERMISSIONS` map, and a `require_permission()`
factory.

```python
USERS_READ, USERS_WRITE,
PROJECTS_READ, PROJECTS_WRITE,
TASKS_READ, TASKS_WRITE,
ANALYTICS_READ,
CALENDAR_READ, CALENDAR_WRITE,      # Phase 4
KNOWLEDGE_READ, KNOWLEDGE_WRITE,    # Phase 5
```

Phases 8 and 9 added no member: Developer, Learning and Career are guarded by
`analytics.read`, so there is no `developer.write`, `learning.write` or `career.write`
whose grant would duplicate it. `tests/test_permissions.py` asserts the complete member
set, so a new capability cannot be added without extending that test.

A route names the **capability** it guards, never a role:

```python
@router.patch(
    "/me",
    response_model=UserRead,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
```

Four decisions in that module are load-bearing:

- **A map, not a `roles`/`permissions` join table.** The role set is tiny and fixed
  (`user`, `admin`) and is chosen in the file, not by an operator at runtime. A table
  would buy mutability nobody has a use for and cost a query on every protected
  request, plus a class of bug where the database says one thing and the code assumes
  another. When the role set stops being fixed, the map is the only thing that has to
  change: the call sites already ask for a `Permission`, never for a role.
- **It fails closed.** `permissions_for()` returns an empty set for an unrecognised
  role instead of raising. A role value that has drifted out of step with the module is
  a *data* problem, and raising on every protected endpoint would turn it into a 500
  for every user. Denying is the correct degraded behaviour: the request is refused,
  the incident is visible, and nobody's working session breaks.
- **It runs after authentication, not instead of it.** An anonymous request must not
  be able to tell "you are not signed in" from "you may not do that".
- **`role` values are duplicated from the ORM as plain strings.** `app.core` must stay
  importable without pulling in the declarative base, or Alembic's `env` — and every
  other consumer of the module — would pay for a full model import. The duplication is
  the price of the layering and is stated in both files.

`PATCH /users/me` carries `USERS_WRITE` even though ownership is implicit, because
the route has no path parameter and the token alone answers *whose*. The permission
answers *whether the capability exists at all* — removing the dependency would not make
the route safer, only unguarded, and would leave a future route added next to it with
no obvious place to hang the check.

### Ownership answers 404, not 403

`SessionService.revoke` fetches with `get_by_id_for_user(session_id, user_id)` —
scoped in the query — and raises `NotFoundError` on a miss.

Fetching by id alone and checking ownership afterwards would be an IDOR: any
authenticated user who guessed a session id could revoke another user's session. And
because "exists but not yours" and "does not exist" would then produce *different*
answers, the endpoint would also be a probe for which session ids are real. Scoping
the query makes another user's session indistinguishable from one that never
existed. This is the rule for every caller-scoped row from here on; `forbidden` stays
reserved for a capability the role map does not grant.

### Password policy

One validator, attached to the `Password` type itself:

```python
Password = Annotated[str, Field(max_length=128, ...), AfterValidator(validate_password_strength)]
```

Attaching it to the type rather than per field is what makes it impossible for a
schema to forget: registration, password change and password reset all accept the same
`Password`, so all three reject the same values. `password_min_length()` is resolved
on *every call* from settings rather than captured at import, so the value stays a
deployment setting and a test can exercise a stricter policy.

The minimum length is deliberately **not** in the `Field`: it is a setting, not a
constant, and duplicating it there would let the advertised schema and the enforced
rule disagree.

A "special" character is anything that is neither a letter nor a digit, so **a space
counts**. Enumerating a fixed punctuation set would be the textbook rule and the
wrong one: it rejects good passphrases and good non-ASCII symbols while adding nothing
an attacker does not already consider. The property worth enforcing is "this is not one
word from a dictionary", and a separator satisfies that as well as `!` does.

`PASSWORD_RULES` and `password_rule_status()` expose the rule ids, labels and
per-rule satisfaction to the client, which renders them as a live checklist. The
backend and the frontend therefore agree on *which* rules there are and in what
order, instead of each keeping its own list. The client copy is a UX affordance and
never an authority — a value that passes every browser check can still be rejected
server-side.

**One known one-way divergence.** The browser's digit rule is the Unicode property
escape `\p{Nd}` (decimal digits); Python's `str.isdigit()` also accepts other numeric
categories, `²` for instance. The client is therefore slightly **stricter** than the
server for exotic characters. The reverse never happens, so this can only cost a user
one confusing round trip, never a rejected password they were told was fine.

### Token digests: why SHA-256 and not bcrypt

Refresh and password-reset tokens are stored as SHA-256 digests and compared with
`hmac.compare_digest`, while passwords use bcrypt at cost 12. The reason is the
**input**, not the algorithm:

| | Password | Refresh / reset token |
| --- | --- | --- |
| Entropy | Low, human-chosen, guessable | 256 bits of cryptographic randomness |
| Attack on a leaked hash | Dictionary / brute force | Preimage of a 256-bit value |
| What bcrypt's work factor buys | Everything | Nothing — there is no dictionary to slow down |
| What it costs | ~250 ms, acceptable once per sign-in | ~250 ms on **every request that touches a session row**, including every refresh |

The raw token is never persisted and never logged. `token_fingerprint_matches()`
compares with `hmac.compare_digest` rather than `==` because an ordinary string
comparison short-circuits on the first differing byte, and the length and timing of
that difference is a weak but free-to-remove oracle for a stored digest.

### Password reset

Implemented for real, because NEXUS is local-first and ships no mail service — a
local install that cannot recover its own account is broken, not strict.

```text
POST /auth/password/forgot      → 202 {"accepted": true, "dev_token": "…"|null}
POST /auth/password/reset       → 204, every session revoked
```

| Property | Rule |
| --- | --- |
| Response shape | Identical for a known and an unknown address. There is no 404 |
| `dev_token` | The raw token, **non-production only**, so the OpenAPI shape does not change between environments. It is a bearer credential for a full account takeover; `AuthService.request_password_reset` returns `None` whenever `settings.is_production` |
| Storage | `password_reset_tokens.token_hash` is the SHA-256 digest. Rows are never deleted on use or on expiry — the `users` foreign key cascade stays the only way a row disappears, and an attempt to reuse a spent token stays distinguishable from one that never existed |
| Redemption | Unknown, spent, expired and wrong-type tokens all answer with the identical 401 |
| Sessions | **Every** session is ended, including ones that existed when the reset was requested. This is the recovery path for a compromised account; leaving one behind would leave the compromise in place behind a new password |
| Why no email enumeration defence by throttling | `rate_limited` is still a reserved code with no limiter; the equal-body guarantee is doing the work instead |

### The audit trail

`audit_logs` records twelve security-relevant events, written by the **services**, not
the routers — so the same event is recorded whether it was triggered by an HTTP
endpoint or by a background job later.

```text
user_registered            user_login           user_login_failed
user_logout                password_changed     password_reset_requested
password_reset_completed   session_created      session_revoked
sessions_revoked_all       account_updated      account_deleted
```

Four properties of the table are deliberate and worth stating:

- **`record()` is best-effort and never raises.** Audit writing is observability, not
  business logic, and it must not be able to deny service. If the audit table is
  unavailable, a service that propagated the error would refuse to let *anyone* sign in
  — an attacker who can fill the table would take authentication down for every user.
  A failed write is logged at WARNING with `exc_info` and returns `None`. The cost is
  that a broken trail is silent at the request level, which is exactly why the failure
  is logged rather than swallowed. The one thing a caller must not do is *retry*: a
  write that failed partway is not safe to assume did not happen.
- **`user_id` is `ON DELETE SET NULL`.** A failed sign-in against an address with no
  account is exactly the event worth recording and has no user row to point at, so the
  column is nullable; and deleting an account must not take its own security history
  with it. `UserService.delete_account` records `account_deleted` *before* the row goes,
  so the surviving record still names who it was. `sessions` and
  `password_reset_tokens` cascade, which is right: they are live credentials, not
  history.
- **No `updated_at`.** `AuditLog` deliberately does not use `TimestampMixin`; the
  mixin's `updated_at` column and its `onupdate` hook would both be lies for a row
  whose entire value is that it has not changed. A write-only guarantee is also a
  retention guarantee — purging old audit data is an explicit operation rather than a
  side effect of editing a row.
- **`metadata` may only ever receive non-sensitive, already-sanitised values.** It is
  JSONB, written verbatim, rendered into exports and retained longer than the sessions
  it describes. Nothing is filtered on the way in, because a redaction list eventually
  misses the one field that matters. No audit row may contain a password, a token, or a
  hash of either.

`AUDIT_LOG_RETENTION_DAYS` is **declared but not enforced**: there is no pruning job,
so the table grows for as long as the install lives. The setting states the policy and
gives the value somewhere to be displayed.

### Revocation store

```python
class RevocationStore:          # backend/app/services/auth_service.py
    async def revoke(self, jti: str, expires_at: datetime) -> None
    async def is_revoked(self, jti: str) -> bool
```

An in-process dictionary keyed by `jti`, guarded by an `asyncio.Lock`, purged of
entries whose token would have expired anyway. Both entry points purge, not just
the lookup: a logout-only workload never calls `is_revoked`, so purging on read
alone would let the bound depend on authenticated traffic continuing. Purging on
`revoke()` too means the dictionary only ever holds revocations that are still
live. The interface is deliberately narrow so a later phase can back it with Redis
without touching the auth service. It is a module singleton
(`get_revocation_store()`) and therefore **per process**; see
[Process model](#11-process-model).

**What it covers, and what it does not.** Phase 2 narrowed its job. It still denylists
**access**-token `jti`s, so logout denylists the access token and `AuthenticatedUser`
consults it. It says nothing about refresh tokens or sessions: those are
database-backed and therefore restart-durable and shared, which the in-memory dict is
not. An access token whose session has been revoked stays cryptographically valid until
it expires, but any refresh against the dead session is refused — so the exposure
window is bounded by `ACCESS_TOKEN_EXPIRE_MINUTES`, not by
`REFRESH_TOKEN_EXPIRE_DAYS`.

---

## 7. Persistence

### Engine and session

| Property | Value |
| --- | --- |
| Driver | `postgresql+psycopg` (psycopg 3, binary wheel) |
| Engine | One per process, created lazily by `get_engine()`, disposed in the lifespan `finally` — and only if one was ever built (see below) |
| Pool | `QueuePool`, `pool_pre_ping=True` — a stale connection is detected on checkout rather than at query time |
| Tuning | `DB_POOL_SIZE` (5), `DB_MAX_OVERFLOW` (10), `DB_POOL_TIMEOUT` (30 s), `DB_POOL_RECYCLE` (1800 s) |
| Probe | `check_database_connection()` is bounded by `DB_PROBE_TIMEOUT_SECONDS` (default 3), independently of `DB_POOL_TIMEOUT` |
| Session | `autoflush=False`, `autocommit=False`, `expire_on_commit=False` — response models read attributes after commit without a reload |
| Request scope | `get_db()` yields one session per request, rolls back on any unhandled exception, always closes |

`expire_on_commit=False` is what lets `UserRepository.create()` return a populated
instance without a second round trip; it still calls `refresh()` because
`created_at` / `updated_at` come from server defaults.

### Engine lifetime

The lifespan's shutdown half reads the module-global `db_session._engine`
directly and disposes it only when it is not `None`. Calling `get_engine()` there
instead would be wrong in the other direction: the engine is created *lazily*,
so an application that never issued a query — and an in-process client, which
never runs the lifespan's own startup path — would have an engine built from the
default settings purely so the shutdown hook could close it. Reading the global
keeps "dispose what was created" honest.

### The database health probe

`check_database_connection()` opens a connection and runs `SELECT 1` under
`asyncio.timeout(settings.db_probe_timeout_seconds)`, reporting `False` — which
the readiness endpoint renders as `database.status: "unavailable"` — on overrun
or on any exception.

`DB_POOL_TIMEOUT` would not have been enough, and the distinction is worth
stating because the two numbers sit next to each other in `.env.example` for no
apparent reason. `pool_timeout` bounds how long a caller *waits for a pooled
connection to become available* — an exhausted pool, with `pool_size` +
`max_overflow` all in use. It says nothing about how long the connection then
takes to be established. A filtered port, a TLS negotiation that never
completes, or a wedged server with the pool empty passes straight through
`pool_timeout` and blocks until the OS gives up on the TCP timeout, minutes
later. The readiness endpoint is precisely the call that must not hang: it is
what a container healthcheck and a "should I wait or give up" UI both poll.

The `finally` block closes the connection unconditionally, because a connect
aborted by the timeout never reaches `__aenter__` and so never runs `__aexit__`
to hand it back.

### Model base

`app/db/base.py` supplies the two mixins every table gets:

| Mixin | Columns | Why |
| --- | --- | --- |
| `UUIDPrimaryKeyMixin` | `id UUID PK`, default `uuid.uuid4` | Generated application-side, so the id is known before flush and no sequential volume leaks. |
| `TimestampMixin` | `created_at`, `updated_at`, `now()`, timezone-aware | Immutable creation, mutable update, both server-side defaults. |

`Base.metadata` is the single source of truth for autogenerate. `users.email`
uniqueness lives on a unique index (`ix_users_email`), because SQLAlchemy folds
`unique=True, index=True` into the index rather than emitting a separate
constraint — the migration records that explicitly with a comment. `users.username`
does the same.

### The Phase 2 tables

| Table | Purpose | Delete behaviour |
| --- | --- | --- |
| `users` | The account. Five columns added by `0002` | — |
| `sessions` | One row per device sign-in; holds the SHA-256 digest of the *current* refresh token | `ON DELETE CASCADE` from `users` — it is a live credential, not history |
| `password_reset_tokens` | One row per outstanding reset request, single-use via `used_at` | `ON DELETE CASCADE` from `users` |
| `audit_logs` | Append-only security trail, twelve event types | `ON DELETE SET NULL` from `users` — **the trail outlives the account** |

Those four are the whole of the Phase 2 schema. Phases 3–9 added the module tables on
top of them — `projects`, `tasks`, `tags`, `activities`, `planner_*`, `calendar_events`,
`work_sessions`, `availability_*`, the `knowledge_*` family, the `analytics_*` family,
`risks`, `recommendations`, the four `git_*` tables of `0008`, and the six
learning/career tables of `0009`. Each revision's own docstring states its delete
semantics; §17 covers the two that are product decisions rather than defaults.
`tests/test_migrations.py::test_the_migration_built_every_table_in_the_metadata` asserts
that every table in `Base.metadata` exists in the migrated schema, and
`test_autogenerate_reports_no_drift` asserts the two agree completely.

Two index decisions are worth stating because both are cases where the obvious
index is the wrong one. `ix_sessions_user_id` is deliberately the *only* index on
the session lookup path: both real queries — "list this user's live sessions" and
"revoke this user's live sessions" — filter on `user_id` first, and `revoked_at` is
then applied to the handful of rows that index returns. A composite
`(user_id, revoked_at)` would duplicate a prefix that is already indexed, in a
table where one account holds a few dozen rows at most; an index on `revoked_at`
alone would be worse, because revoking is always scoped to one user so almost no
query could use it.

`audit_logs` uses `metadata_` as the **attribute** and `metadata` as the column:
the bare name is reserved on the declarative class, where it already means the
collection of mapped columns. Callers use the attribute; raw SQL and Alembic see
the column.

### Migrations

| Property | Value |
| --- | --- |
| Tool | Alembic, `script_location = migrations`, `prepend_sys_path = .` |
| URL | `sqlalchemy.url` is **empty** in `alembic.ini`; `migrations/env.py` injects `get_settings().sqlalchemy_database_uri`. No credentials are tracked by git. |
| Engine | Async, `NullPool`, driven through `connection.run_sync()` because SQLAlchemy's async engine cannot run migrations directly |
| Event loop | `asyncio.run(..., loop_factory=nexus_loop_factory)` — the same factory the server and the test suite use |
| Scope | `include_object` restricts autogenerate to the `public` schema and excludes `alembic_version`, `spatial_ref_sys` |
| Rendering | `render_item` emits `postgresql.UUID(as_uuid=True)` so generated migrations state the dialect explicitly |

Current chain: **ten revisions**, one linear head, `0001_initial_create_users` →
`0002_phase2_identity_sessions` → `0003_phase3_projects_tasks` → `0004_phase4_planner` →
`0005_phase5_knowledge` → `0006_phase6_analytics` → `0007_phase7_intelligence` →
`0008_phase8_developer_intelligence` → `0009_phase9_learning_career` →
`0010_learning_career_integrity`.
Models are deliberately **not** imported by migration files, so editing `app/models/`
cannot rewrite history.

`0010` is the only revision added after the nine phase revisions, and it repairs three
invariants `0009` stated in prose but did not enforce: `uq_career_evidence_source_identity`
rebuilt as a *partial* unique index with `NULLS NOT DISTINCT` (a plain table-level unique
constraint over six columns, two of which are usually null, deduplicated nothing at all under
PostgreSQL's default `NULLS DISTINCT` btree semantics);
`learning_activities.skill_id` changed from `ON DELETE CASCADE` to `ON DELETE SET NULL`; and
`ix_activity_events_owner_created` added on the table the activity feed reads. Existing
duplicates are deleted before the index is built, because the rule was never enforced and a
database that has been through `0009` may hold several rows for one derived identity.

`0002` does three things autogenerate cannot do for it, and each is a case where
the generated draft would have been wrong:

| Operation | Why it is hand-written |
| --- | --- |
| `op.alter_column("users", "full_name", new_column_name="display_name")` | Autogenerate cannot see a rename; it emits drop + add, which loses the data |
| `username` added nullable → backfilled → `SET NOT NULL` | `users` is not empty by then. A `server_default` would be fewer statements but hands every row the *same* literal, so the unique index below fails on any database with more than one user. The chosen backfill is `left(split_part(email,'@',1), 15) \|\| '_' \|\| left(replace(id::text,'-',''), 16)` — 32 characters exactly, deterministic, and unique by construction because of the UUID suffix |
| `role`, `avatar_url`, `password_changed_at` | `password_changed_at` in particular must be added with **no** default: rows that predate it have nothing truthful to backfill |

Commands, all from `backend/`:

```bash
python -m alembic upgrade head            # apply everything
python -m alembic downgrade -1            # roll back one revision
python -m alembic downgrade base          # empty the schema
python -m alembic revision -m "message"   # new, empty revision
python -m alembic check                   # autogenerate drift check
```

`alembic check` is the command for drift, and
`backend/tests/test_migrations.py::test_autogenerate_reports_no_drift` asserts the
same thing with `compare_type` and `compare_server_default` enabled, so drift is a
test failure rather than a discovery. Both need a live PostgreSQL — `nexus_test` — and
both now run against one: `alembic upgrade head` is applied by the `test_database_url`
fixture on every integration session. The two commands were also run by hand during the
final remediation pass, against a live PostgreSQL 16.2, because two phase reports had
claimed they had never been:

```text
$ cd backend && .venv/Scripts/python.exe -m alembic current
0010 (head)

$ cd backend && .venv/Scripts/python.exe -m alembic check
No new upgrade operations detected.
```

Two database-free checks exist, and they are not the same claim:

- `test_migrations.py::test_the_migration_chain_is_linear_and_has_a_single_head`
  parses the version files and asserts the literal chain
  `["0010", "0009", …, "0001"]` with head `0010`. It needs no database, and it does mean
  a new revision comes with a one-line test update — a deliberate pin, not an oversight.
- `tests/test_migration_ddl.py` renders the chain **offline** (`as_sql=True` into a
  buffer), parses the emitted SQL and compares every `CREATE TABLE` column, foreign key
  and index against `Base.metadata`. That is real evidence that *the DDL the migrations
  emit and the DDL the models describe are the same schema* — and it is **not** the same
  as having applied them. It is the cheaper check, and it now runs alongside the applied
  migration rather than in place of it.

### Extensions

`docker/postgres/init/10_extensions.sql` runs on first init of an empty volume and
creates `pg_trgm` and `unaccent` — prerequisites for the Phase 4 search work
(fuzzy matching, accent-insensitive comparison). It creates nothing else: no
tables, no roles, no seed data. Alembic owns the schema.
`scripts/create_test_database.py` enables the same two extensions in `nexus_test`
so a native-PostgreSQL test run exercises the same features as the container; a
missing contrib module is a warning, not an error. The suite provisions the same
two extensions itself, in `conftest.py:_ensure_extensions`, because the script
is optional and may never have been run on a given machine — `CREATE EXTENSION
IF NOT EXISTS` is idempotent, so running both is harmless.

### Test database

The Postgres image ships only `postgres`, `template0` and `template1`.
`nexus_test` is created by `tests/conftest.py` (`_ensure_database_exists`, via an
AUTOCOMMIT connection to the `postgres` maintenance database, because
`CREATE DATABASE` cannot run inside a transaction) and by
`scripts/create_test_database.py`.

Both refuse to run if `TEST_DATABASE_URL` resolves to the application database,
and that refusal is load-bearing: `truncated_database` runs `TRUNCATE … RESTART
IDENTITY CASCADE` over every managed table before each test, so a
`TEST_DATABASE_URL` left pointing at the real database would empty it. The
conftest check (`_assert_separate_test_database`) calls `pytest.exit` rather than
skipping, because the conftest is the thing that actually truncates — the script
is optional, may never have been run, and is not evidence of anything.

---

## 8. Configuration model

`Settings` is a `pydantic-settings` `BaseSettings`, `extra="ignore"`,
case-insensitive, returned by an `lru_cache`d `get_settings()` singleton. No
module other than this one reads `os.environ` for application settings.

### Derived values

| Property | Rule |
| --- | --- |
| `sqlalchemy_database_uri` | `DATABASE_URL` if set, otherwise assembled from `POSTGRES_*` with the user and password percent-encoded |
| `test_sqlalchemy_database_uri` | `TEST_DATABASE_URL` if set, otherwise the same URL with `_test` appended to the database name |
| `cors_origin_list` | `CORS_ORIGINS` split on commas, empty entries dropped |
| `is_production` / `is_testing` | Derived from `ENVIRONMENT` |

An explicit URL always wins over the parts. That is what lets the same `.env`
drive both a host install and the compose stack, where the URL must name the
`postgres` service rather than `127.0.0.1`.

### Security and session settings

Phase 2 added five. Their defaults are documented in `.env.example`; the two
comments worth repeating here are the ones a reader is most likely to mis-set.

| Setting | Default | Note |
| --- | --- | --- |
| `password_min_length` | 8 | Read on **every** policy evaluation rather than captured at import, so it stays a deployment setting |
| `password_reset_expire_minutes` | 30 | Short because the token is a bearer credential for a full account takeover |
| `session_absolute_lifetime_days` | 30 | Bounds a session independently of rotation |
| `max_active_sessions` | 20 | Evicts the oldest on sign-in; never refuses the sign-in |
| `audit_log_retention_days` | 400 | **Declared, not enforced.** No pruning job exists |

### Production guards

A `model_validator` refuses to construct settings when `ENVIRONMENT=production`
and either `SECRET_KEY` is still the `dev-insecure-change-me` placeholder or
`DEBUG=true`. Both fail at import time, before uvicorn binds a port. Generate a
real key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

A second normaliser turns an all-whitespace `LOG_FILE` into `None`, so a blank
line in `.env` does not create a file named `" "`.

### The one validator that is not about production

`_validate_productivity_weights` refuses to construct `Settings` at all unless
the four weights
`analytics_productivity_weight_{completion,deadline,consistency,focus}` sum to
100 and none is negative.

The productivity score is presented as a percentage, so those four numbers are
its denominators: a set summing to 90 would report an "80/100" that is really
"80/90", and one summing to 120 would report a score of 100 having awarded 120
points. Neither is a tuning choice; both are a broken scale.

The obvious third option — silently renormalising the weights to 100 — is the
one this validator exists to refuse. Rescaling would hide that the configured
numbers were wrong, and a formula whose constants cannot be argued with is
precisely what the block of four settings in `config.py` was written to
prevent. `get_settings()` is an `lru_cache`d singleton constructed at import
time, so **the failure is a refused process start**, with a message naming all
four values and the required total:

```text
The analytics productivity weights must sum to 100; they sum to 95.0
(analytics_productivity_weight_completion=30.0, ..._deadline=25.0,
..._consistency=20.0, ..._focus=20.0).
```

Contrast `analytics_comparison_windows`, in the same module, which deliberately
does the opposite: unparsable entries are dropped rather than raised, because
they feed a list of *suggested* period lengths and a typo in one of them should
cost the user that suggestion rather than take the app down. The difference is
the difference between an invariant and a preference.

### Bounds that refuse an unbounded query

Fifteen settings from Phases 4 and 6 bound a computation rather than describe a
preference. They were undocumented until the final remediation pass; the full
tables are in [`development.md`](development.md) §11.5 and in the README's
[environment-variable table](../README.md#environment-variables). The shape they
share:

| Group | Settings | What the bound is for |
| --- | --- | --- |
| Planner | `planner_lookahead_days` | How far forward the scheduler searches, so a large backlog stays a bounded walk of availability rather than a scan |
| Planner | `planner_max_session_minutes`, `planner_min_session_minutes`, `planner_max_suggestions_per_task` | The shape of one proposal, so one large task cannot fill the horizon ahead of a task that is due tomorrow |
| Analytics | `analytics_max_range_days` (366), `analytics_rebuild_max_days` (180) | Every windowed aggregate scans the owner's whole history; an unbounded range is the one query shape these indexes cannot serve |
| Analytics | `analytics_default_range_days`, `analytics_comparison_windows` | What a request that names no dates gets. A week is the shortest span that can distinguish a habit from a one-off |
| Planner | `planner_default_timezone`, `planner_day_start_hour`, `planner_day_end_hour` | Which *day boundaries* a view spans. Every stored instant is UTC regardless; `08:00–20:00` is a daytime window the scheduler assumes when told nothing, not a working-hours claim |

### Where the variables live

Full tables are in the [README](../README.md#environment-variables); the only one
worth repeating here is the split between the settings object and the entrypoint:
`NEXUS_HOST`, `NEXUS_PORT` and `NEXUS_RELOAD` are read directly by
`backend/run.py`, not by `Settings`, because uvicorn needs them before the
application exists.

---

## 9. Observability

The stdlib `logging` module, not `structlog`. Phase 1 needs exactly one thing
structlog would have provided — per-request context on every record — and
`contextvars` plus a custom `Formatter` does it in a few dozen lines without
adding a dependency to the runtime image (the reasoning is recorded in
`backend/app/core/logging.py`).

| Setting | stdout | file (`LOG_FILE`) |
| --- | --- | --- |
| `LOG_JSON=true` (container default) | one JSON object per line | JSON lines |
| `LOG_JSON=false` | colourised human line, dimmed when stdout is not a TTY or in production | JSON lines |

`configure_logging()` also clears the handlers of `uvicorn`, `uvicorn.access`,
`uvicorn.error` and `sqlalchemy.engine` so they propagate to the root handlers,
and disables `uvicorn.access` outright. It runs in the lifespan with `force=True`
so early import-time records are already formatted.

### Redaction

`redact()` walks mappings and sequences to a depth of 8 (a cyclic payload cannot
stall a logging call) and replaces the value of any key that folds — lowercased,
hyphens to underscores — onto `REDACTED_KEYS`: `password`, `hashed_password`,
`token`, `access_token`, `refresh_token`, `authorization`, `secret`, `secret_key`,
`api_key`, `cookie`, `session`, `csrf_token` and relatives. The JSON formatter
redacts the **key/value pair**, not the value alone, so a scalar
`password="hunter2"` passed through `extra` is still caught.

The request-body preview is the one place `redact()` is *not* enough, because it
can only walk a parsed structure. `_body_preview` therefore renders only a JSON
object or array — re-serialised through `redact()` and capped at
`_MAX_LOGGED_BODY_CHARS` (2000) — and summarises everything else by size:

| Body | Preview |
| --- | --- |
| JSON object or array | The redacted JSON, truncated to 2000 characters with a `...<truncated>` marker |
| Form-encoded, multipart, malformed JSON, a bare JSON scalar | `<not logged: not a JSON object, N bytes>` |
| Body larger than the capture limit | `<not logged: truncated at the capture limit, N bytes>` |
| Empty | `""` |

The point is that a form body carries its credentials in a shape no key-based
scrub can be trusted to recognise — `email=…&password=…` looks like one opaque
string — so quoting it at all would leak it. Recording the size and, separately,
the `content_type` field on the same record tells an operator what arrived
without telling them what was in it.

The cap on *capture* is a different limit from the cap on *preview*. Only when
`LOG_REQUEST_BODY=true` does `BodyCaptureMiddleware` buffer at all, and it keeps
at most `_MAX_CAPTURED_BODY_BYTES` (256 KiB) while still reading and replaying
every byte, so the handler's body is unchanged and one large upload cannot
become unbounded memory in the log path. `body_length` records the true size.

### Access log fields

| Field | Notes |
| --- | --- |
| `method`, `path` | Path is structural and is never redacted. |
| `status_code` | Read back from the outgoing response-start message; 500 when no response started. |
| `duration_ms` | `time.perf_counter`, rounded to 2 places. |
| `client_ip` | Left-most `X-Forwarded-For` entry, else the socket peer. |
| `query` | Redacted. `_redact_query` replaces the *value* of any parameter whose key folds onto `REDACTED_KEYS`, up to the next `&` or `#`, so `?token=abc&page=2` logs as `token=***redacted***&page=2`. `redact()` cannot do this — it walks mappings and sequences, and a raw query string reaches the log untouched without this. |
| `user_agent` | Raw header. |
| `body`, `content_type` | Only when `LOG_REQUEST_BODY=true`; see above. |

Level selection is in `_access_log_level`: ERROR for ≥500, WARNING for ≥400 or
for a duration at or above `SLOW_REQUEST_MS`, INFO otherwise.

---

## 10. Event loop and platform constraints

This is the single most important platform fact in the codebase.

psycopg 3 implements async I/O with `loop.add_reader`. asyncio's default loop on
Windows is `ProactorEventLoop`, which does not provide it, so every database
operation fails with:

```text
InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode
```

`SelectorEventLoop` provides the reader callbacks psycopg needs, and is already
the default on Linux and macOS — so selecting it explicitly there changes
nothing, and the platform branch that used to guard it only hid a bug. The whole
fix is now two functions with no branching at all
(`backend/app/core/event_loop.py`):

```python
def selector_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())

def nexus_loop_factory() -> asyncio.AbstractEventLoop:
    return selector_loop_factory()
```

The second half of the rule is *how* the loop is constructed, and it is not a
style preference. The previous version read:

```python
def nexus_loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return selector_loop_factory()
    return asyncio.new_event_loop()
```

`asyncio.new_event_loop()` routes through the **active event-loop policy**, and
in the test suite that policy is `_NexusEventLoopPolicy`, whose `new_event_loop`
delegates straight back to `nexus_loop_factory`. So on Linux and macOS the
factory called itself until the stack ran out — an infinite recursion on every
non-Windows run of the suite, the server, and `alembic`. It was invisible here
purely because the suite runs on Windows, where the branch took the
non-recursive path and the bug never executed. It is a real bug that a passing
test run could not have caught, which is why `tests/test_event_loop.py` now
installs a deliberately delegating policy and calls `asyncio.new_event_loop()`
with it, asserting the loop comes back.

Constructing `asyncio.SelectorEventLoop(selectors.SelectSelector())` directly
bypasses the policy entirely, which is what makes the factory safe to hand to a
policy that points back at it.

The factory is applied in exactly three places, and must not be duplicated
anywhere else:

| Consumer | Mechanism |
| --- | --- |
| API server | `backend/run.py` passes `loop="app.core.event_loop:nexus_loop_factory"` to `uvicorn.run` (an import path, because uvicorn resolves `--loop` that way) |
| Migrations | `migrations/env.py` calls `asyncio.run(..., loop_factory=nexus_loop_factory)` |
| Test suite | `tests/conftest.py` installs an `asyncio.DefaultEventLoopPolicy` subclass whose `new_event_loop` delegates to the factory, at import time so it is in place before pytest-asyncio snapshots the policy |

The third consumer is what makes the direct construction mandatory rather than
merely tidy: two of the three call sites are safe either way, but the policy
consumer is a fixed point, and only a factory that never asks the policy a
question can sit inside one.

**Consequence:** `cd backend && python run.py` is the supported way to start the
API on every OS. A bare `uvicorn app.main:app` starts on Windows and then fails on
every query; `scripts/dev.sh` uses `python run.py` for exactly this reason and
says so in a comment.

---

## 11. Process model

The backend runs **one** worker per container. That is not a default to be tuned —
it follows from two per-process pieces of state:

| State | Consequence of a second worker |
| --- | --- |
| `RevocationStore` singleton | An **access-token** logout recorded in worker A is invisible to worker B, so that access token stays usable until it expires. |
| Lazy engine + session factory | One pool per worker; `NEXUS_RELOAD=true` forks a second process that would carry its own pool and its own empty denylist. |

Phase 2 shrank the first row considerably: refresh tokens and sessions are
database-backed, so *session* revocation is shared across workers by construction
and the blast radius of the in-memory denylist is bounded by
`ACCESS_TOKEN_EXPIRE_MINUTES` (60 by default) rather than by the seven-day refresh
lifetime. The single-worker constraint still stands, now for the access-token
window and for the pool.

`run.py` therefore defaults `NEXUS_RELOAD` to `false` and Compose forces it to
`"false"`. Horizontal scaling, when it is ever needed, comes from running more
containers behind a load balancer — not from more workers in one container. The
backend Dockerfile notes the same constraint next to its `CMD`.

---

## 12. Frontend architecture

### Provider stack

`main.tsx` renders `StrictMode → AppProviders → RouterProvider`, and
`app/providers.tsx` nests:

```text
QueryClientProvider      server state, 30 s staleTime, no refetch on focus
└── ThemeProvider        resolves light|dark|system, applies `.dark` to <html>
    ├── TooltipProvider  Radix, 200 ms delay
    │   └── AuthBootstrap  calls store.hydrate() once, then renders
    └── Toaster          a SIBLING of the routed tree, inside ThemeProvider
```

`Toaster` being a sibling rather than a child is not an accident of the JSX. It
has to sit *outside* `AuthBootstrap`'s subtree so a notification survives a route
throwing into its error boundary, and outside the guards so an auth failure — the
one case where there may be no page at all — is still reportable. Inside
`ThemeProvider` so a toast inherits the palette of the page it reports on.

The theme is applied in `useLayoutEffect`, and a blocking inline script in
`index.html` applies the persisted theme *before* the bundle executes. The script
can only read a plain `light` / `dark` string, so the store mirrors the resolved
theme into `nexus-theme` while the raw preference (`light` | `dark` | `system`)
lives in the persisted `nexus.theme` key. Without the mirror, a stored light theme
would paint dark for one frame on every reload.

### Routing

`createBrowserRouter` in `routes/router.tsx`; every page is a `lazy()` import
declared in `routes/lazy-pages.ts` so the router file stays a pure route table.

| Route group | Guard | Elements |
| --- | --- | --- |
| `/` | — | Redirects to `/dashboard` |
| `AppLayout` children | `RequireAuth` | dashboard, projects, project detail, tasks, planner, planner-month, knowledge, note detail, concept detail, search, analytics, risks, recommendations, developer, developer repository detail, learning, career, assistant, experiments, settings, `*` → not found |
| `RequireAnonymous` children | `RequireAnonymous` | `/login`, `/register`, `/forgot-password`, `/reset-password` |

Password recovery sits on the anonymous branch deliberately: there is no session
to authenticate with, and a signed-in user has no reason to see either screen. The
route table stays token-free — `ResetPasswordPage` reads the token from a
`?token=` query parameter with `useSearchParams` and is the single place that does,
so a reset token never reaches a loader or an error message.

While `status === 'initializing'` (a persisted session is being verified against
`GET /auth/me`) both guards render a branded `BootScreen` rather than redirecting,
which is what stops a signed-in user from seeing the login page on every reload.
`RequireAuth` remembers the intended path in `location.state.from`;
`RequireAnonymous` sends an authenticated user to `/dashboard`.

### The module registry

`frontend/src/features/modules/catalog.ts` declares every destination once:
`to`, `label`, header `summary`, long-form `vision`, `phase`, icon, capabilities,
metrics and search keywords. The sidebar, the command palette, the page headers
and the placeholder bodies all render from it, and `getModule()` throws on an
unknown path, so a route that drifts from the registry fails loudly. Adding a
module is a single-file change plus a route entry.

`ALL_NAV_ITEMS` — fourteen modules plus Settings — is the palette's destination
list: 15 entries, filtered with a 120 ms debounce and driven by ArrowUp/ArrowDown,
Enter and Escape. `NAV_GROUPS` arranges them into five groups — Overview, Work,
Intelligence, Growth and Platform — so a module added in Phase 9 did not have to invent
a sixth.

### Data access

| Layer | File | Rule |
| --- | --- | --- |
| Transport | `lib/api-client.ts` | Framework-agnostic typed `fetch` wrapper: base URL, bearer injection, timeout (`DEFAULT_TIMEOUT_MS` = 30 000), `AbortSignal` support, and a single `ApiError` type. No React, no React Query. |
| Endpoints | `services/*.ts` | One exported function per endpoint. `errors.ts` holds the single `unknown → ApiError` conversion every caller shares; the endpoint files are `auth.ts`, `sessions.ts`, `users.ts`, `health.ts`, `work.ts` (projects, tasks, tags, activity), `planner.ts`, `knowledge.ts`, `analytics.ts`, `risk.ts`, `developer.ts` and `learning.ts`. No module imports `fetch` — everything goes through `lib/api-client.ts`. |
| Server state | `app/query-client.ts` | Retries suppressed for 4xx (a rejected request does not become accepted by asking again); transport failures get two attempts, which covers "the backend is still starting". |
| Client state | `stores/*.ts` | Zustand: `auth-store` (persisted to `nexus.auth`, `status` deliberately not persisted), `theme-store`, and `toast-store` (ephemeral, deliberately not persisted). |

The auth store wires two hooks onto the shared client at module load:

```ts
apiClient.setTokenGetter(() => useAuthStore.getState().accessToken)
apiClient.setUnauthorizedHandler(() => useAuthStore.getState().renewAccessToken())
```

The second one is the 401-recovery interceptor: an expired access token no
longer strands a signed-in user on whatever page they were on. `ApiClient`
catches a 401, asks the handler for a fresh token, and replays the original
request once.

That only works if concurrent callers do not each try to rotate. Refresh tokens
are **single-use server-side** (see [Auth flows](#6-authentication-and-authorisation)),
so a burst of parallel 401s firing four refreshes would have three of them
present an already-spent token, get a 401 back, and tear down the pair the
winner had just stored. So the refresh path is guarded at three points:

| Guard | Where | Covers |
| --- | --- | --- |
| `recovery` (a shared promise) | `ApiClient`, `recoverToken()` | A burst of 401s from different requests shares one renewal. |
| `refreshInFlight` (a shared promise) | `auth-store`, `refreshSession()` | `hydrate()` and any 401 recovery share one rotation of the one refresh token. |
| `hydrateInFlight` (a shared promise) | `auth-store`, `hydrate()` | A StrictMode double-mount cannot start two verifications. |

The three live at module scope, not in store state, so they outlive any single
store read.

`hydrate()` verifies a persisted session against `/auth/me`; on 401 it attempts
one refresh and retries. A refresh failure is then split by outcome, because a
backend that did not answer says nothing about the validity of the token:
`unreachable` (transport error, timeout, or 5xx) keeps the stored pair and
reports a retryable state, and only an actual rejection (`unusable`) clears the
session. Signing the user out over a network blip would cost them a re-login for
nothing. Register logs the new account in immediately rather than showing a
second form. Logout clears local state in a `finally` — a failed server call must
never trap the user in a signed-in shell.

A sign-out, a rejected session and a fresh sign-in all announce themselves
through `onSessionChange()`, which the query layer subscribes to so cached data
cannot outlive the session that fetched it. The store does not import the query
client to do it.

### Shell and design system

`components/layout/app-shell.tsx` owns the responsive frame: a fixed sidebar above
`lg` that can collapse to icons (persisted under `nexus.sidebar.collapsed`), and an
off-canvas sheet with an overlay button below it. The sidebar width is published as
a CSS variable so the content gutter animates with it; the sheet state is derived
from the breakpoint, so resizing to desktop cannot strand an open sheet.

Design tokens are HSL channel triplets in `src/index.css`, mapped to Tailwind
utilities in `tailwind.config.ts` with `<alpha-value>` placeholders.
`components/ui/` holds the primitives.

**Some of them are hand-rolled, and that is a consequence of what is installed.**
`frontend/package.json` ships seven Radix packages — avatar, dropdown-menu, label,
scroll-area, separator, slot, tooltip — and nothing else. There is no
`@radix-ui/react-dialog`, `-tabs`, `-progress`, `-switch` or `-select` in the lockfile,
so Phase 2 wrote those five by hand rather than adding a dependency:

| Primitive | Why hand-rolled | What it had to reproduce |
| --- | --- | --- |
| `dialog.tsx` | no Radix dialog | Focus trap, focus restore on close, `Escape` to dismiss, overlay dismissal, scroll lock, `aria-modal` |
| `tabs.tsx` | no Radix tabs | Full ARIA: `role="tablist"/"tab"/"tabpanel"`, `aria-selected`, `aria-controls`, `aria-labelledby`, arrow-key navigation and a **roving tabindex** |
| `progress.tsx` | no Radix progress | `role="progressbar"` with `aria-valuenow/min/max` |
| `alert.tsx` | no Radix alert | `role="alert"` / `role="status"` by severity |
| `switch.tsx` | no Radix switch | `role="switch"` + `aria-checked` on a `<button>` |
| `select.tsx` | no Radix select | **A native `<select>`, deliberately** — see below |

`select.tsx` is worth singling out because it looks like an omission and is not.
A hand-built listbox is where accessibility goes to die: type-ahead, type-ahead
buffer, `Home`/`End`, arrow wrapping, screen-reader announcements of the active
option and of the number of options, and the platform picker on touch are all
hard, and the native element gets every one of them from the OS for free. The cost
is that it cannot be styled like the rest of the system on every platform — which is
the correct trade for a setting that picks a colour scheme.

`toast-store.ts` / `toast.tsx` / `toaster.tsx` are likewise hand-built: a Zustand
store plus an ARIA live-region announcer. The store is deliberately **not**
persisted — a toast is about something that just happened, and one that survives a
reload is a lie.

The account work added three feature modules that are pure domain logic, not
components: `features/auth/password-rules.ts` (a mirror of the server policy, with
the documented `\p{Nd}` divergence), `password-strength.ts` (scoring) and
`session-labels.ts` (turning a user agent into "Chrome on Windows"). The last one is
why `sessions.user_agent` is stored verbatim: the same derivation must produce the
same label every time, which it cannot if the string is normalised on the way in.

### The settings page

`/settings` is a five-tab surface — Profile, Account, Security, Sessions,
Preferences — with one panel per tab in `features/settings/`. It is the first page
in the product that is *about* the account rather than *showing* one, and it is
where most of the Phase 2 endpoints are exercised: profile update, password change,
per-session revoke, "sign out everywhere", and a password-protected account
deletion behind a confirmation dialog.

`Security` and `Sessions` are deliberately separate tabs. A password change revokes
every *other* session, so doing it and looking at the session list are one action and
one question, and splitting them across tabs would make the consequence of the first
invisible while the second is on screen.

### Build

`manualChunks` in `vite.config.ts` splits `node_modules` by first match, narrow
rules before broad ones: `charts`, `icons`, `radix`, `router`, `data`, `react`.
Routes are additionally split by the `lazy()` calls above.

Exact byte sizes of the current `frontend/dist/assets/*.js`, uncompressed, as
built by `npx vite build`:

| Chunk | Bytes |
| --- | --- |
| `charts` (largest vendor) | 432 148 |
| `react` | 222 425 |
| `radix` | 113 444 |
| `index` (entry) | 104 155 |
| `router` | 92 238 |
| `learning-page` (largest route) | 67 842 |
| `career-page` | 52 538 |
| `icons` | 46 668 |
| `data` | 37 965 |
| `developer-page` | 35 303 |
| `knowledge-page` | 34 610 |
| `planner-page` | 32 662 |
| `quick-add` | 32 220 |
| `settings-page` | 31 313 |
| `developer-repository-page` | 22 140 |
| `dashboard-page` | 17 530 |
| `module-page` (the three placeholders) | 2 754 |
| `not-found-page` | 2 314 |

The `charts` group is now the largest chunk because Analytics shipped and pulls in
recharts; it was reserved for a module that did not exist when this list was first
written. Route splitting is still doing its job: every live module page has its own
chunk, and the three remaining placeholder routes all share the single 2,754 B
`module-page` chunk because their code lives in `ModulePage` and the registry. A
placeholder chunk growing by kilobytes would be the signal that something page-specific
had crept into it.

The dev server and `vite preview` (4173) share the same proxy configuration, so a
previewed production bundle behaves like the reverse proxy that will eventually
front the API.

---

## 13. Testing architecture

### Backend

```bash
# from backend/
python -m pytest                        # 2270 collected, needs nexus_test
python -m pytest -m "not integration"   # 1029 collected, 1241 deselected, no database required
```

Those are **collection** counts, from `pytest --collect-only`. The last full run before the
final remediation pass was 2136 passed and 9 failed; the nine were fixed by the engineers who
own those files, and one full run is scheduled once the pass lands. Collection says what the
suite contains, not that it passes, and this document does not conflate the two.

`pytest.ini` sets `testpaths = tests`, `pythonpath = .`, `asyncio_mode = auto`,
function-scoped event loops (`asyncio_default_fixture_loop_scope` and
`asyncio_default_test_loop_scope`), `--strict-markers --strict-config`, and
registers the `integration` marker.

**A `DeprecationWarning` from `app.*` is an error**, and the filter order is what
makes that true:

```ini
filterwarnings =
    ignore::DeprecationWarning
    error::DeprecationWarning:app.*
```

pytest *prepends* each ini entry to `warnings.filters`, and the **last** match
wins — so a blanket `ignore` listed second would swallow the escalation and make
the rule dead. The blanket ignore therefore has to come first.
`tests/test_warnings.py` asserts all three facts: that a warning attributed to an
`app.*` module raises, that one from elsewhere stays ignored, and that the
app-scoped rule really does precede the blanket ignore in `warnings.filters`.
That last assertion is the one that catches someone reordering the file into a
rule that looks right and does nothing.

Counts below are from `pytest --collect-only` and `pytest --collect-only -m integration`
run against this tree, so they are what the suite *collects*; §[What has not been
run](#what-has-not-been-run) records what has actually been executed.

| File | Offline | Integration | Covers |
| --- | --- | --- | --- |
| `test_migration_ddl.py` | 154 | — | Renders the whole migration chain to SQL offline and compares every emitted `CREATE TABLE` column, foreign key and index against `Base.metadata` |
| `test_learning_schemas.py` | 146 | — | Phase 9's learning domain at both ends of its surface: schemas and routers' contracts, with no database |
| `test_learning_career_routers.py` | — | 124 | The Phase 9 HTTP surface end to end: two routers |
| `test_risk_recommendation.py` | — | 117 | What a stored risk turns into: a suggestion with a stated reason, raised once |
| `test_career_schemas.py` | 87 | — | Phase 9's career domain at both ends of its surface |
| `test_risk_scoring.py` | 79 | — | The risk scoring formulas as pure arithmetic |
| `test_risk_api.py` | — | 75 | The Phase 7 surface end to end: risks, recommendations, detection |
| `test_learning_api.py` | — | 70 | The Phase 9 learning HTTP surface end to end |
| `test_developer_git.py` | — | 72 | The git engine, against real repositories built in the test |
| `test_analytics_scoring.py` | 63 | — | The analytics scoring formulas as pure arithmetic |
| `test_developer_api.py` | — | 60 | The Phase 8 HTTP surface end to end |
| `test_password_policy.py` | 55 | — | Each rule in isolation, the length bound, and that the message states the actual configured number |
| `test_career_api.py` | — | 46 | The Phase 9 career HTTP surface end to end |
| `test_developer_metrics.py` | 45 | — | The developer metric formulas as pure arithmetic — no database and no `.git` directory |
| `test_auth_phase2.py` | — | 42 | The Phase 2 auth surface end to end: sessions, password change, reset, logout-all |
| `test_permissions.py` | 42 | — | The eleven-member `Permission` enum, `ROLE_PERMISSIONS`, the fail-closed unknown-role path, `has_all` / `has_any` |
| `test_logging.py` | 42 | — | JSON and human formatter shape, `REDACTED_KEYS` folding, hyphenated keys, `ContextFilter` request-id propagation |
| `test_middleware.py` | 41 | — | Access-log fields, body-preview redaction, query redaction, capture cap vs. verbatim replay, non-default `create_app(settings=…)` wiring |
| `test_security.py` | 39 | — | JWT issue/verify, `type` enforcement, tamper and expiry rejection, bcrypt behaviour, `hash_token` / `token_fingerprint_matches` |
| `test_models.py` | 37 | — | Model-level invariants that need no database |
| `test_regressions_security.py` | 32 | 5 | The security fixes, with the evidence each one rests on |
| `test_developer_repository.py` | — | 36 | `DeveloperRepository` against the real test database |
| `test_learning_repository.py` | — | 36 | `LearningRepository` against the real test database |
| `test_analytics_scores_api.py` | — | 35 | The five analytics score endpoints, end to end over HTTP |
| `test_learning_metrics.py` | 35 | — | The learning metric formulas as pure arithmetic |
| `test_analytics_edge_cases.py` | — | 27 | What analytics says when the data is thin, wrong-shaped or gone |
| `test_analytics_export_api.py` | — | 27 | CSV export over HTTP: the manifest, the download, and what the file promises |
| `test_developer_schema.py` | 26 | — | Model/migration agreement for Phase 8, rendering `0008` offline |
| `test_analytics_daily_metrics.py` | — | 25 | The `daily_metrics` tier: one row per user per UTC day, and nothing else |
| `test_analytics_learning_api.py` | — | 25 | The learning, knowledge and ML-feature reads, end to end over HTTP |
| `test_learning_gaps.py` | 24 | — | The skill-gap formulas as pure arithmetic — the module that cannot lie about a level |
| `test_risk_detection.py` | — | 32 | One detection pass: what it writes, what it refuses, what it closes |
| `test_analytics_overview_api.py` | — | 23 | The analytics dashboard surface, end to end through HTTP |
| `test_analytics_projects_api.py` | — | 23 | Per-project and task-level analytics through the HTTP layer |
| `test_career_repository.py` | — | 21 | `CareerRepository` against the real test database |
| `test_learning_service.py` | — | 20 | The Phase 9 learning service end to end: what it stores, refuses, says |
| `test_regressions_users.py` | 20 | — | The user-account fixes |
| `test_analytics_privacy.py` | — | 19 | User isolation on the analytics surface, end to end over HTTP |
| `test_sessions.py` | — | 19 | Session issue, rotation, the session cap, listing and revocation |
| `test_developer_service.py` | — | 16 | The Phase 8 service end to end: what it stores, refuses, repeats |
| `test_error_handling.py` | 16 | — | The 5xx path: what a client may see, `X-Request-ID` on a 500, the no-echo rule for a 5xx `detail`, unmapped statuses as 4xx |
| `test_account.py` | — | 15 | Profile update, account deletion, the cascade and `SET NULL` behaviour |
| `test_errors.py` | — | 15 | The envelope, the code table, `FORBIDDEN_FRAGMENTS` — the shared leak assertions other files import |
| `test_auth.py` | — | 14 | Register / login / refresh / logout / me end to end |
| `test_career_service.py` | — | 14 | The Phase 9 career service end to end: what it writes, refuses, hides |
| `test_config.py` | 14 | — | Settings assembly, derived URLs, production guards, the `get_settings` cache |
| `test_password_reset.py` | 12 | — | The reset *policy* — issuance shape, digest comparison, single-use — without touching the database |
| `test_rbac.py` | — | 12 | The permission gate end to end, including the admin listing fixture |
| `test_repositories.py` | — | 9 | SQL against the real test database |
| `test_session_database_lock.py` | — | 9 | The guard that keeps two pytest sessions out of one test database |
| `test_health.py` | 5 | 3 | Liveness never touching the database, header propagation, and the **degraded** path; the detailed endpoint and the lifespan are integration |
| `test_developer_git_integration.py` | — | 7 | Phase 8 against **real git repositories**: the five shapes that break a scanner |
| `test_migrations.py` | — | 5 | Linear single-head chain (pinned to `["0009", …, "0001"]`), schema present, no drift |
| `test_event_loop.py` | 3 | — | The factory survives a delegating policy, builds a `SelectorEventLoop` with `add_reader`, and yields a fresh loop each call |
| `test_warnings.py` | 3 | — | The `filterwarnings` rules above |
| `test_knowledge_api.py` | — | 30 | The Phase 5 knowledge surface end to end: notes, concepts, resources, links, bookmarks, categories and documents over HTTP |
| `test_planner_api.py` | — | 19 | The Phase 4 surface end to end: availability, the planner, scheduling and the events a booked block writes |
| `test_task_project_api.py` | — | 18 | The Phase 3 surface end to end, including the cascade rules that make deleting a task destroy the sessions and calendar events filed against it |
| `test_analytics_feature_snapshot.py` | — | 16 | The Phase 10 ML hook: one exactly-derived feature row per task, `null` where nothing was observed, and `schema_version` beside the matrix rather than inside it |
| `test_rate_limit.py` | — | 13 | The in-process fixed-window limiter: the credential budget, `OPTIONS` exemption, the store ceiling, and the 429 envelope |
| `test_availability_atomicity.py` | — | 11 | `PUT /availability` replaces a week or changes nothing — checked from a second connection, because a rolled-back delete is invisible to the session that wrote it |
| `test_task_integrity.py` | — | 8 | The Phase 3 invariants that used to be enforced only in prose: a subtask cannot move to another project, and neither can a card that already has subtasks |
| `test_developer_features.py` | — | 8 | `developer_features.v1`: a repository that has never been scanned has no row at all, rather than a row of zeros |
| `test_analytics_pagination.py` | — | 8 | `GET /analytics/projects` as the `Page[T]` envelope, counters under `meta` |
| `test_migration_0010.py` | — | 7 | What `0010` changes, applied and read back: the partial `NULLS NOT DISTINCT` index, the `SET NULL` on `learning_activities.skill_id`, and the activity-feed index |
| `test_career_query_efficiency.py` | — | 5 | The career aggregates read an index rather than a scan, asserted from the plan |
| `test_documentation_claims.py` | — | — | The documentation is a claim surface too: every `Settings` field documented, the `Page[T]` count matching `app.openapi()`, and the superseded figures gone |
| **Total (67 files)** | **1029** | **1241** | |

Counts are from `pytest --collect-only -q` and `pytest --collect-only -q -m "not integration"`,
run from `backend/` during the final remediation pass. They are a snapshot: a test file added
after it was written will not be in the table. The first fifty-five rows are the suite as the
Phase 9 report left it; the twelve below them are what the remediation wave added, and every
one of those twelve exists because a real defect was found — the three blockers, the Phases 3–5
coverage gap, and the honesty rules the Phase 10 contract depends on.

The whole of `test_migrations.py` is `integration`-marked, so even the chain-shape
assertion is deselected offline; `test_migration_ddl.py` is the database-free
substitute for the *DDL agreement* half of it, and with 154 tests it is now the largest
offline file in the suite.

Two of these deserve a note on what they changed.

`test_health.py`'s **degraded** path is real coverage, not inspection: it patches
`check_database_connection` where the router imported it — `app.api.v1.health`,
not `app.db.session`, or the real probe stays in place and reaches for the
database the test exists to avoid — and asserts the endpoint answers **200** with
`status: "degraded"` and `database.status: "unavailable"`. Liveness is asserted
the same way, by making any database access an `AssertionError`.

`test_error_handling.py` exists because the default `httpx.ASGITransport` sets
`raise_app_exceptions=True`: an exception escaping the app is re-raised inside the
test, so the rendered 500 is never visible and the application's own catch-all
handler is untestable. Both it and the shared `non_raising_client` fixture turn
that off (`ASGITransport(app=app, raise_app_exceptions=False)`) for exactly the
tests that need to observe a failure.

Fixtures in `conftest.py`:

| Fixture / helper | What it does |
| --- | --- |
| `settings` | Clears the `get_settings` cache before and after each test, so a monkeypatched variable cannot leak. |
| `make_settings` | Builds a `Settings` from explicit environment overrides, clearing the cache on the way out. |
| `test_database_url` (session) | Refuses to start if `TEST_DATABASE_URL` resolves to the application database, creates `nexus_test` if missing, enables `pg_trgm` / `unaccent`, and migrates it to `head`. |
| `engine` (session) | `NullPool` engine on the test database, installed as the application's engine, restored on teardown. |
| `truncated_database` | `TRUNCATE … RESTART IDENTITY CASCADE` over every managed table before each test. |
| `db_session` | A session on the test database; `rollback()` on exit discards only what a test left uncommitted. |
| `offline_client` | A client with no database fixture in scope, for DB-free assertions. |
| `non_raising_client` | A client that observes the rendered 5xx instead of re-raising it (see above). |
| `client` | In-process `AsyncClient` over `app` with the database fixtures in scope. |
| `assert_error_envelope` | Asserts the status, the exact outer and inner key sets, the code, a non-empty message, and `error.request_id == response.headers["X-Request-ID"]`. |
| `_preserved_logging` | Saves and restores the stdlib logging tree around Alembic's `fileConfig`. |

Two rules the database fixtures exist to enforce:

- **The schema comes from the migration, never from the models.**
  `Base.metadata.create_all` is deliberately never used: a schema built from the
  models would prove nothing about the migration.
- **`TEST_DATABASE_URL` must not name the application database.**
  `_assert_separate_test_database` calls `pytest.exit`, not `pytest.skip`,
  because `truncated_database` really does `TRUNCATE` — a skip here would be a
  guard that silently stops guarding. `scripts/create_test_database.py` refuses
  the same collision, but it is optional and may never have been run.

The test engine uses `NullPool` and is installed as the application's engine for
the whole session. This is required, not an optimisation: `pytest.ini` scopes the
asyncio loop to a single test, so a pooled connection opened under one loop would
be reused under the next.

`ASGITransport` does not run the lifespan, so no test may assume the startup or
shutdown hooks have fired — the `engine` fixture stands in for what they would
have done. `test_health.py::test_app_lifespan_runs_without_error` is the one
test that drives `lifespan_context` explicitly.

### Frontend

```bash
# from frontend/
npm test               # vitest run — 645 tests in 44 files, all passing
npm run typecheck      # tsc -b
npm run lint           # eslint .
npm run build          # tsc -b && vite build
```

Vitest runs in `jsdom` with `src/test/setup.ts`. The suite grew from 10 files in
Phase 1 to **44 files / 645 tests**, and its shape changed with them: the
hand-rolled primitives that carry real behaviour (`dialog`, `tabs`), the auth pages
(`login`, `register`, the shared form-error flattening), the password rules and strength
scoring, the toast store and an end-to-end smoke pass over the real routes, and then one
`*.test.tsx` beside every live page — analytics, developer, developer-repository,
learning, career, recommendations, risk-center — each of which pins the rules §17 states
about levels, absences and server-named field errors.

That frontend figure is a real pass count: `npm test` was run end to end during the final
remediation pass and reports 44 files and 645 tests, all passing. It is the only number in
this document that is a pass count rather than a collection count, because the frontend suite
touches no database and does not contend for the `nexus_test` advisory lock.

### What is not covered

Single-user local-first product: no load, concurrency, migration-from-an-older-
schema, or browser-matrix testing. No audit-log retention test, because there is no
retention job to test.

### What has not been run

Every number in the two command blocks above was produced on Windows against a **native
PostgreSQL 16.2**. The suite creates and migrates `nexus_test` itself, so the repository,
session, account, RBAC, password-reset, migration, drift and
detailed-health assertions are **exercised**, not merely written. `alembic upgrade head` is
applied by the `test_database_url` fixture on every integration session, and
`test_autogenerate_reports_no_drift` asserts that the live schema and `Base.metadata`
agree. The offline half of the claim is equally real: the 1029-test
`-m "not integration"` subset runs with PostgreSQL stopped, so nothing that could run
without a database has been quietly marked `integration` to go green.

What is **not** verified is anything that needs a container. The Phase 2 text that used
to sit here said the database-backed assertions were unverified; that was true of the
document it replaced and is not true now — the 1241 `integration` tests exist and they run
against a real server. What it got right, and what still matters, is that
the offline subset alone says very little about a migration or a new query — which is why
the full run is the one to read before trusting a change to a model, a repository or a
revision.

- **`alembic upgrade head` used to be render-only evidence, and no longer is.**
  `tests/test_migration_ddl.py` renders the chain offline and compares the emitted DDL
  against the models. That is real evidence about the DDL, and it is still **not** the
  same as having applied it — a migration can render correctly and still fail on a real
  server (locks, permissions, a constraint that already exists). The difference is that
  the applied case is now covered too: the fixture upgrades `nexus_test` to `head` and
  `test_autogenerate_reports_no_drift` compares the result.
- **`docker-compose.yml` has never been executed by `docker compose`**, and Docker is not
  installed in this development environment at all — so the three images have never been
  built either. The file is structurally validated by `scripts/verify_compose.py`, which
  confirms that all 14 interpolated variables are documented in `.env.example`, and which
  cannot tell you the stack actually starts. The `postgres:16-alpine` image and the
  `docker/postgres/init/` extension script have not run either; the suite provisions
  `pg_trgm` and `unaccent` itself in `conftest.py`, which is precisely why a native-server
  run is green even though the container's first-init script never executed.

And the Linux/macOS behaviour of the event-loop factory is asserted by a unit
test that reproduces the recursion, not by having run the suite on either
platform. The bug it covers was real precisely *because* it could not be caught
here.

---

## 14. Container topology

`docker-compose.yml`, project `name: nexus`, three services, every value
interpolated as `${VAR:-default}` from `.env` — nothing is hardcoded.

| Service | Image / build | Depends on | Start command |
| --- | --- | --- | --- |
| `postgres` | `postgres:16-alpine` | — | entrypoint, with `pg_isready` healthcheck and the init script mounted read-only |
| `backend` | build `./backend` | `postgres` **healthy** | `alembic upgrade head && exec python run.py` |
| `frontend` | build `./frontend` | `backend` **healthy** | `npm run dev` (Vite dev server, not a static bundle) |

Three decisions are worth reading in the file itself:

- **The backend migrates on start.** The image ships `migrations/` and Alembic but
  runs neither at build time nor as an entrypoint, so a fresh volume would serve
  requests against an empty schema. `upgrade head` is a no-op once current.
- **The frontend image runs the dev server**, not a built bundle, and sets
  `VITE_API_BASE_URL=/api/v1` so the browser is same-origin and the Vite proxy
  forwards to `VITE_DEV_PROXY_TARGET=http://backend:8000`.
- **Healthchecks gate startup**, so `depends_on: condition: service_healthy`
  orders postgres → backend → frontend instead of racing them.

The backend Dockerfile is multi-stage: an `awk` extraction at the `DEV MARKER`
line strips pytest and ruff from the runtime image, and the runtime stage runs as
uid 10001 with `libpq5` and `curl` only.

---

## 15. Extension roadmap

<a id="extension-roadmap"></a>

The seams below exist today. None of the *destinations* is implemented; the
"Today" column says what is already in place to reach it.

| Seam | Today | Later | Touch points that must not change |
| --- | --- | --- | --- |
| Access-token revocation | `RevocationStore`, in-process | Redis-backed denylist | `RevocationStore` interface, `get_revocation_store()` |
| Audit retention | `audit_log_retention_days` declared; **no pruning job** | a scheduled delete of rows older than the window | `AuditRepository`; the job must not delete `account_deleted` rows before the account is gone |
| Horizontal scaling | one worker per container | more containers behind a load balancer | sessions and reset tokens are already database-backed and shared; the access-token denylist is what still forces the single worker |
| Background work | none | worker process | `scripts/` for process orchestration; jobs need the same event-loop factory |
| Search | `pg_trgm` + `unaccent` enabled | retrieval index over Knowledge — **Phase 5 shipped the knowledge tables, the retrieval index itself did not** | extension availability is already a prerequisite, and the extensions are already installed |
| Local LLM | none | Ollama-backed assistant | never leaves the machine; the catalog already fixes the Phase 9 contract |
| Repository analysis | **live since Phase 8** — see [§17](#17-developer-learning-and-career) | background rescans | read-only, from disk; `git_scan_runs` is the seam a scheduler writes to |
| Module data | `catalog.ts` registry | the remaining placeholder modules (Search, AI Assistant, Experiments) | add a route entry and a catalog entry; nothing else |
| Role set | `user` / `admin` in a Python map | custom roles, per-tenant roles, delegated scopes | call sites ask for a `Permission`, never a role, so only `ROLE_PERMISSIONS` and the duplicated role constants change |
| Module permissions | eleven capabilities, all granted to `user`; every module router guards its reads and writes | none outstanding | `require_permission()` is the only place a capability is checked |
| ML training | **in flight since Phase 10** — `backend/ml/` is a separate package with its own entry point and its own interpreter; see [§19](#19-phase-10--ml-training) | a model registry, and serving a trained model from the API | nothing under `backend/app/` imports `backend/ml/`, so the trained artifact can be introduced later without the API depending on torch |

Phase numbering is not invented here — it is the `phase` field in
`frontend/src/features/modules/catalog.ts`, and the module list it drives is the
single source of truth for the sidebar, the palette and the roadmap. That module
numbering is a different axis from the platform phasing in the README's Status
table, which is why both are called "Phase 2".

---

## 16. Design decisions

| Decision | Rationale | Cost accepted |
| --- | --- | --- |
| Start the API with `run.py`, not bare `uvicorn` | The event loop must be chosen before the app exists; one factory, three consumers | An extra entrypoint file; a bare `uvicorn` command silently misbehaves on Windows |
| `127.0.0.1` in URLs, never `localhost` | `localhost` → `::1` on Windows and psycopg is IPv4 only; the failure is a hang, not an error | Slightly less portable-looking configuration |
| Empty `sqlalchemy.url` in `alembic.ini` | Credentials must never live in a tracked file | One extra indirection through `get_settings()` |
| Migrate on container start | A fresh volume cannot serve traffic against an empty schema | Startup pays for `upgrade head`; the healthcheck `start_period` covers it |
| Empty `sqlalchemy.url`, explicit DDL, no model import in revisions | Editing a model must not rewrite history | Drift is possible by hand — caught by `alembic check` and a test |
| Service layer never imports FastAPI | Keeps business rules testable and the dependency graph acyclic | Errors travel as exceptions instead of return values |
| Repository never raises domain errors | One translation point; `IntegrityError` becomes `409` in one place | Services must be prepared for raw DB exceptions |
| `bearer_scheme(auto_error=False)` | FastAPI's built-in failure is a 403 with the wrong shape; ours is the shared 401 envelope | Every auth dependency must handle the `None` credentials case |
| `type` claim + expected-type check | Stops a refresh token being used as a bearer credential | Two token types to reason about |
| In-memory `RevocationStore` | Real logout in Phase 1 with no new dependency | Per-process only; forces single-worker, and caps future scale-out. Phase 2 shrank the job to access tokens only — refresh tokens and sessions are database-backed — so the exposure window is now `ACCESS_TOKEN_EXPIRE_MINUTES` rather than the refresh lifetime |
| `expire_on_commit=False` | Response models read attributes without a reload | Callers must not assume a refresh happened |
| `pool_pre_ping=True` | A database restart should not surface as a request error | One extra round trip per checkout |
| Stdlib logging, not structlog | One thing needed (request context), achieved with `contextvars` + a `Formatter` | Handlers, formatters and a filter to maintain by hand |
| Install `RequestContextMiddleware` **above** `ServerErrorMiddleware` | A middleware registered with `add_middleware` sits inside the layer that renders a 500, so it never observes that response and cannot stamp `X-Request-ID` on it — every 500 would carry a `request_id` in its body and none in its header | Overriding `build_middleware_stack` is a private-ish Starlette hook, and the middleware must stay pure ASGI and stamp the header on `http.response.start` itself |
| Header and error body stamped from one id, on every status | They come from the same binding in the same middleware call, so `error.request_id == X-Request-ID` cannot drift — including on the 500 path | None; this is a consequence, not a compromise |
| Decoy bcrypt verify on an unknown e-mail | A shared error message hides the account from the response *text*; only a comparable runtime hides it from the response *clock* | An unknown address pays the full bcrypt cost (~250 ms) too — a deliberate DoS surface on a single-user local app |
| Purge the revocation denylist on `revoke()` as well as on lookup | A logout-only workload never calls `is_revoked`, so read-time purging alone makes the bound depend on authenticated traffic continuing | A full dict scan inside the write lock; irrelevant at Phase 1 volume |
| A 5xx never echoes its `detail` | It is text the application did not author for a client, and it can carry SQL, paths or credentials | A legitimate 5xx `detail` set by a developer is silently discarded client-side; it is logged instead |
| `internal_error` reserved for 5xx; unmapped 4xx → `bad_request` | The frontend branches on `code`; reporting an oversized upload or a wrong content type as a server fault sends the user down the wrong recovery path | Several 4xx statuses share one code, so `code` alone cannot distinguish a 413 from a 402 |
| Build the event loop directly, never via `asyncio.new_event_loop()` | That call routes through the active policy, and the test suite's policy points back at this factory — the two are a fixed point | One loop type everywhere, including platforms where it was already the default |
| One event-loop factory, three consumers, never duplicated | A second copy is a second thing to forget when the psycopg constraint changes | None; the cost is remembering the three call sites |
| `DB_PROBE_TIMEOUT_SECONDS` alongside `DB_POOL_TIMEOUT` | `pool_timeout` bounds waiting for a *pooled* connection; it says nothing about the TCP/TLS handshake behind one, which is exactly how a filtered port fails | Two tunables that look like the same knob and are not |
| Dispose the engine only if one was built | `get_engine()` is lazy; calling it in the shutdown hook would build an engine from the default settings purely to close it | Reaching into `db_session._engine` rather than through the accessor |
| `lifespan` reads `app.state.settings` | `create_app(settings=…)` already builds CORS, docs URLs and the API prefix from those settings; logging and the database probe used the global singleton and disagreed | One extra attribute, and `app.state` becomes load-bearing |
| Contextvar first, `request.state` fallback | Now that the middleware sits outside `ServerErrorMiddleware`, the catch-all runs with the contextvar still bound — so the contextvar is the *primary* source and `request.state` is a safety net for a path that no longer exists | Two sources of truth for one id |
| `non_raising_client` for tests that assert on a rendered 5xx | The default transport re-raises, which makes the application's own catch-all handler unobservable | One more client fixture, and a rule about which tests use which |
| `pytest.exit` when `TEST_DATABASE_URL` names the application database | `truncated_database` really does truncate; a skip would be a guard that silently stops guarding | A configuration mistake stops the whole session rather than skipping the affected tests |
| Frontend 401 recovery + single-flighted refresh | An expired access token should not strand the user, but refresh tokens are single-use server-side — concurrent refreshes would destroy the winner's fresh pair | Three module-level in-flight promises to reason about, and a replay path that must not loop |
| Same-origin `/api/v1` under Compose | Keeps CORS and cookies out of the picture in dev; the proxy is an accurate stand-in for a reverse proxy | Two `VITE_API_BASE_URL` values depending on mode |
| Module registry as the single declaration | Sidebar, palette, headers and placeholders cannot drift apart | Adding a module still needs a route entry |
| React Query for server state, Zustand for client state | Caching/polling and session/theme are different problems | Two state libraries |
| Placeholder pages that render an em dash | No fabricated data; the UI states plainly what does not exist | Screenshots and demos look emptier than a mock would |
| **One `sessions` row per device; rotation replaces the token hash** | "Sign out everywhere" becomes one `UPDATE` rather than a search through a token table, and the sessions screen can show a revoked device as signed out rather than gone | The row holds only the *current* digest, so there is no token history and a rotated-away token is indistinguishable from any other invalid one — which is the intended answer |
| **`max_active_sessions` evicts the oldest instead of refusing the sign-in** | A stolen refresh token must not be able to accumulate sessions, but refusing a legitimate sign-in is a worse failure than signing out an old device | An attacker and the owner are treated identically, so a user can lose a session they still wanted |
| **Sessions have an absolute lifetime independent of rotation** | A rotating refresh token would otherwise let a session that is never signed out of outlive any actual sign-in event | A user who refreshes on a phone every few days is signed out at 30 days regardless |
| **SHA-256 for tokens, bcrypt for passwords** | The input decides: a password is low-entropy and needs a work factor; a 256-bit signed token has no dictionary, so a work factor buys nothing and would cost ~250 ms on every session read | Two hashing schemes to reason about, and a reviewer has to know which is which to catch a mistake |
| **`permissions` derived from `role`, never stored, never in a table** | The role set is tiny and fixed; a join table would buy runtime mutability nobody has a use for and cost a query per protected request | A role value that drifts from the map is only caught at the point of use — mitigated by failing closed rather than raising |
| **Unknown role → empty permission set, not an exception** | A drifted role value is *data*, and raising would turn it into a 500 on every protected endpoint for every user | The failure is silent at the request level; it is visible in logs, and the request is denied |
| **`require_permission()` runs after authentication** | An anonymous request must not be able to tell "not signed in" from "not permitted" | Every protected route carries two dependency hops |
| **A row the caller does not own answers 404, not 403** | A 403 confirms the id exists, turning the endpoint into a probe for real ids; 404 is what a non-existent id returns too | The two cases are indistinguishable even to a legitimate operator debugging by hand |
| **`role` is a string, not a Postgres enum** | Adding an enum value needs `ALTER TYPE … ADD VALUE`, which cannot run inside a transaction block on some deployment paths — exactly the kind of migration that fails halfway through a deploy | The database no longer rejects a misspelled role on its own |
| **`audit_logs.user_id` is `ON DELETE SET NULL`, `sessions` cascade** | A failed sign-in against an unknown address is the event worth recording and has no user row; and an account's security history must outlive the account, or a deletion cannot be investigated | Two different delete semantics to remember, and orphan audit rows by design |
| **`AuditService.record` never raises** | An audit sink must not be able to deny service — a full table would otherwise take authentication down for every user | A broken trail is silent at the request level, so the failure is logged at WARNING rather than swallowed |
| **`AuditLog` has no `updated_at`** | The mixin's `onupdate` hook would rewrite the timestamp of the one row that must not change; a write-only guarantee is also a retention guarantee | The table has one timestamp instead of two, which looks inconsistent next to the others |
| **No filtering of `audit_logs.metadata` on write** | A redaction list eventually misses the one field that matters, and this column is retained longer than the sessions it describes | Every caller is trusted; the cost of getting it wrong is unrecoverable |
| **Password reset returns `dev_token` outside production** | NEXUS is local-first and ships no mail service; a local install that cannot recover its own account is broken, not strict | The field carries a full-takeover credential in dev, and it is the field's presence in the schema that proves the guard has to exist |
| **The hand-rolled dialog/tabs/progress/alert/switch primitives** | No Radix package exists for them in `frontend/package.json`, and no new dependency was in scope | Each one carries accessibility behaviour Radix would have handled — focus trap, roving tabindex, ARIA wiring — and each is now code this repository must maintain and test |
| **A native `<select>` rather than a hand-built listbox** | Type-ahead, `Home`/`End`, wrap-around and screen-reader announcements are all hard to get right, and the platform gets them right | It cannot be styled like the rest of the system on every platform, which is the correct trade for a colour-scheme picker |
| **Security and Sessions are separate settings tabs** | A password change revokes every other session, so the consequence of the action and the list it affects should be on screen together | One more tab to navigate |

---

## 17. Developer, Learning and Career

Phases 8 and 9 added three subsystems on top of the layering above. They are described here
because each one introduced something structural the rest of this document does not cover:
a subprocess boundary, a module that cannot touch the database, and two surfaces whose
subject is a *person's self-description* rather than their activity.

> Sections 1–16 were written against Phase 2. The claims that Phases 3–9 falsified —
> the migration chain, the test counts, the route inventory, the permission set, the
> extension roadmap, and the "no PostgreSQL in this environment" note — have since been
> corrected in place. What remains Phase-2-shaped is the *narrative*: several sections
> still explain a decision in the terms it was made in, and a module that arrived later
> is described by §17 and the two phase reports rather than woven back through them.

### 17.1 Where the code lives

```text
app/services/developer/          git.py (subprocess) · metrics.py (pure) · service.py
app/services/learning/           gaps.py (pure)   · metrics.py (pure)   · service.py
app/services/career/                                    service.py
app/repositories/developer.py  learning.py  career.py
app/api/v1/developer.py       learning.py    career.py
migrations/versions/0008_phase8_developer_intelligence.py
migrations/versions/0009_phase9_learning_career.py
```

All three services follow the layering rule in §2: the routers compute no figure and
compose no sentence, and the repositories never raise a domain error.

### 17.2 Developer Intelligence — the subprocess boundary

`app/services/developer/git.py` is the **only** module in NEXUS that starts another
program. Four properties are structural rather than stylistic:

| Property | Rule |
| --- | --- |
| **No `shell=True`, no GitPython, no network** | Every invocation is `asyncio.create_subprocess_exec`, so the arguments never reach a shell parser and there is nothing to inject. The git CLI is a dependency the machine already has, not a package this project vendors |
| **A wall-clock timeout with a kill** | `DEVELOPER_GIT_TIMEOUT_SECONDS` (30). A network-mounted work tree or a filter process waiting on a prompt must come back as an error row with a human sentence, not as a request that never returns |
| **An output byte ceiling** | A pathological repository cannot exhaust memory through a pipe |
| **A stderr sanitiser** | Whatever git puts on stderr is a *sentence* about the repository, never a traceback and never an absolute path from inside the user's home directory |

**Every scan is wrapped, and a failed scan is a row.** `POST
/developer/repositories/{id}/scan` answers **200 whether the read worked or not** — a deleted
directory, a corrupt `.git`, an unreadable network share and a timeout are all
`status='error'` with a sentence. There is no code path on that router where a bad directory
produces a 500. This is the concrete form of the phase's rule that a broken repository must
never break NEXUS.

**The Windows collision.** Two of this codebase's own architectural rules meet here. NEXUS
runs a `SelectorEventLoop` on every platform, because psycopg's async driver needs
`loop.add_reader` and asyncio's Windows default (`ProactorEventLoop`) does not provide it
(§10). But on Windows a `SelectorEventLoop` raises `NotImplementedError` from
`subprocess_exec` — it has no subprocess transport at all. So:

- `_running_loop_can_spawn()` detects the condition as a **class** test rather than a trial
  call, so no exception from an unrelated cause is caught here and mistaken for a
  repository problem.
- `_run_git_on_worker_loop()` runs the **same** `_run_git_here` on a private
  `ProactorEventLoop` from a worker thread. Same function, so the timeout, the ceiling, the
  kill and the sanitiser all still apply — the fallback cannot become a laxer scan.
- The private loop is constructed **directly**, not through the active policy, because the
  policy is `nexus_loop_factory`, which hands back the very loop being worked around.

Cost: one thread hop per git invocation on Windows. On POSIX none of it runs.

**Idempotency is a unique constraint, not a convention.** `uq_git_commits_repo_hash` over
`(repository_id, commit_hash)` is what makes pressing the scan button twice safe. It is a
*full* unique constraint rather than Phase 7's partial one because a commit is not an
episode: the same commit is the same commit forever and there is no "resolved" version of
one. The consequence is visible and correct — a rescan of an unchanged repository reports
`commits_discovered` equal to what git returned and `commits_added` of 0, and that gap is
the proof the upsert worked.

**Two ordering rules inside one method, both load-bearing.** `_record_scan_findings` emits
`COMMIT_DETECTED` only for commits strictly after the stored `latest_commit_at`, so the
high-water mark must be read **before** any write: `update_scan_state` is an
`UPDATE ... RETURNING` with `populate_existing=True` against the same identity-mapped
instance, and reading it afterwards yields *this* scan's newest timestamp, making
`committed_at > high_water_mark` impossible and the event permanently silent. And
`commit_count` accumulates `repository.commit_count + inserted` rather than being assigned
what the scan returned, because an incremental scan returns only the commits after the mark
— assigning it directly would make a repository's commit count *fall* on every rescan.

**No table here could be summed into a measure of time.** There is no `hours`,
`minutes_spent`, `effort` or `focus` column anywhere in `0008`. The counts are commit
objects, days that carried a commit, and lines git counted from a diff.

### 17.3 Learning and Career — two surfaces about a person

Phase 9 is the first phase whose subject is a person's *self-description*. Every decision in
migration `0009` follows from one rule: **NEXUS may never be the author of it.**

#### The honesty control

`skills.level_source` (`user_defined` | `system_estimate`) decides which words the
explanation is *allowed* to use:

```python
LEVEL_SOURCE_PHRASES = {USER_DEFINED: "self-assessed", SYSTEM_ESTIMATE: "system estimate"}
```

`SkillGap.__post_init__` **rejects an explanation that omits the phrase its source requires**
and an explanation carrying no digit — the same technique `RecommendationDraft.__post_init__`
already used in Phase 7. So *"current self-assessed 2/5"* and *"current NEXUS system estimate
of 2/5"* are constructible and *"current 2/5"* is not. The routers close the other door: a
level sent by a client is always stored as `user_defined`, and `PATCH /learning/skills/{id}`
cannot write `evidence_count`, `last_activity_at`, `confidence` or `level_source`.

The neutral gap sentence is *"Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related
learning activities in the last 30 days."* Never *"You are not good at X."*

#### The pure module that cannot lie about a level

`app/services/learning/gaps.py` touches no database, no ORM model, no clock and no request.
It imports exactly one thing from outside — the read-only `SkillLevelSource` vocabulary — and
"Now" is a parameter rather than a call to the clock. So the gap arithmetic is assertable to
the exact value with no PostgreSQL in the picture.

**A gap is computed on read and never stored**, for the reason `app/models/analytics.py`
keeps weekly and monthly metrics derived: a stored copy is a *second answer* to "how far from
my target is this skill?" that could disagree with the dashboard the moment a level was
edited, and the disagreement is always resolved by whichever page the user opened first.

**A measured zero and an absent measurement are two different answers**, and something that
caches them has already thrown one away. `gap=0, available=True` means the target is met — a
real measurement. `gap=0, available=False` means NEXUS has nothing recorded and therefore no
business saying anything. `SkillGap.gap` is typed `int` because the frozen contract freezes
that type, so **the `available` flag is the whole safety mechanism** and `__post_init__`
guarantees it is `False` exactly when a reason is attached.

#### Delete semantics as a product decision

In `0009` every `ondelete` is argued in the migration's own docstring, and the split is the
interesting part:

| Reference | Rule | Why |
| --- | --- | --- |
| `user_id`, all six tables | CASCADE | a row nobody can reach is a row nothing can read |
| `learning_activities.skill_id` | **CASCADE** | the one context reference that cascades. An activity whose only subject is gone is not evidence of anything |
| goal → project / note / skill; activity → goal; evidence → project / skill / repository | **SET NULL** | the recorded trail outlives the intention it was recorded against. Deleting a project must not delete the evidence, and deleting a repository must not delete the user's record that they shipped something |

`DELETE /learning/skills/{id}` therefore cascades while `DELETE /learning/goals/{id}` does
not — and that asymmetry is the design, not an oversight.

#### Two deduplication mechanisms, both using NULL-is-distinct

| Table | Constraint | What it prevents |
| --- | --- | --- |
| `uq_career_evidence_source_identity` over `(user_id, evidence_type, source, project_id, skill_id, repository_id)` | a full unique constraint | a project-derived evidence row being inserted twice |
| `uq_risks_live_identity` (Phase 7) over `(user_id, risk_type, entity_type, entity_id)`, partial | a *partial* index | a risk that resolves and legitimately returns colliding with its own history |

PostgreSQL treats NULLs as distinct in a btree unique index, which is the whole trick: several
manually-added `ACHIEVEMENT` rows (all three FKs null) coexist, while a derived one cannot
be duplicated. Phase 9 uses a full constraint because unlike a risk, evidence has no
"resolved" state.

#### Features, not models

Three feature vectors ship, one per phase, each stamped with a closed schema version:
`developer_features.v1`, `learning_features.v1`, `career_features.v1`. Nothing is trained,
loaded, served or registered. (Phase 10 is the first phase to break that, from outside this
package — see [§19](#19-phase-10--ml-training).) The rule between them is one sentence — **a
figure that could not be computed is `null`, never `0`** — and
`career_features.v1.project_activity` is its worked example: it is null when no repository has
ever been scanned, because `0` would assert that a repository exists and carries no commits
when the truth is that nobody has looked. Inside a training matrix a fabricated zero is
indistinguishable from an observed one.

#### Conventions the three subsystems established

| Convention | Rule |
| --- | --- |
| Literal sub-paths before parameterised routes | Starlette matches in registration order and does not prefer a literal segment over a parameter. `POST /learning/goals/{goal_id}` registered above `/learning/summary` would bind the literal string `summary` to the path parameter and answer 422 about an id that never existed |
| A page-size cap is a rejection | `?limit=500` is a 422. A caller that asked for 500 and received 200 cannot tell a truncated page from a page that was always 200 rows long |
| `PATCH` is `exclude_unset=True` | `None` means "write SQL NULL" downstream. A field the client never sent is not a field the user asked to clear; an explicit `null` is |
| An immutable stamp has one producer | `completed_at` is written only by `POST /learning/goals/{id}/complete`, from the **database** clock |
| An identity column is not editable | `local_path`, `source` and the `*_id` pointers are absent from their `PATCH` payloads, because a re-pointed row becomes a second record |
| The window ceiling belongs to the service | Only `ge=1` is declared on the route; a constant there would answer 422 against a limit the deployment has raised |
| `analytics.read` guards the writes too | No `developer.write`, `learning.write` or `career.write` exists — the permission test asserts the complete member set, and a new one would be granted to exactly the roles `analytics.read` already covers |

---

## 18. Remediation pass over Phases 1–9

Phase 9 shipped, and then an audit ran over everything built so far, and found things worth
finding. Four engineers fixed them on disjoint files; this section records what actually
changed in the architecture, because a reader of §3, §7, §8 and §17 above should not have to
guess which of it was always true and which was repaired.

### Three data-loss and correctness blockers

| Defect | Root cause | What shipped instead |
| --- | --- | --- |
| **Deleting a project destroyed subtasks that belonged to another project.** A task could be re-parented out of its board and then left pointing at a project it was not on; deleting that project cascaded the card away with it, and the card's board — the thing the user filed it under — went with it | The `project_id` a task moved to was never checked against the task's own position in the tree | A subtask cannot be moved into another project at all, and a root card that already has subtasks cannot either. Detaching a subtask and then moving it, and moving a child card to another root card *in the same project*, both still work |
| **`PUT /api/v1/availability` destroyed the user's week.** `replace_for_user` called `delete_for_user`, which **commits**, and only then inserted the submitted rules. The moment the table refused one row, the previous week was already gone and durable: two rules in, one duplicate on the way back, zero stored, and the caller told "conflict" about a change that had silently deleted their calendar. The audit measured exactly that, 2 → 0 | Delete-then-insert across two transactions, with the write already durable before the one that could fail | One transaction. A refused replace leaves the previous week completely intact — and the regression test proves it **from a second connection**, because a rolled-back delete is invisible to the session that wrote it |
| **A deadline sentence named the wrong day.** The due phrase was rebuilt as `detected_at + deadline_in_hours`: a gap the detector measures at the *start* of its pass, added to the instant the row is *written*. Two clocks in one sum, so the answer is `due_date + (written_at - measured_at)` — right to the second on an ordinary pass, and a whole day late on any pass that begins before midnight and writes after it. The next pass repairs the text, which is why it survived: a reason is persisted, so the bad day is stored too | Summing a duration measured against one clock with a timestamp taken from another | The `due_date` **column**, read. The regression test moves `detected_at` a day later and fails on all three horizons, so it does not depend on the suite happening to run at 23:59 |

### Phases 3–5 had no dedicated test modules at all

The audit's other structural finding: projects, tasks, planner, calendar, work sessions and
knowledge had been exercised only incidentally, through the analytics and risk suites that
read their rows. Nothing tested those routers' own contracts.

They do now. `test_task_project_api.py`, `test_planner_api.py` and `test_knowledge_api.py`
were added, along with `test_task_integrity.py` for the tree invariants above and
`test_availability_atomicity.py` for the transactional guarantee. That is where the
cross-project subtask defect was found in the first place: it is a Phase 3 rule that Phase 3
had never written down.

### The versioned feature vector is the Phase 10 contract

This is the rule ML training depends on, and the audit found two places breaking it.

**`feature_snapshot` returned a bare mapping of feature columns with no version key.** It now
returns `{schema_version: "analytics_features.v1", generated_at, task_id, features: {…}}`.

The version sits **beside** the matrix, never inside it, and that placement is the whole
point: `features` is a feature matrix — a positional row of numbers whose every column must be
a feature. A string smuggled into that mapping is not a feature, it is a column a model is
then asked to fit, and it would be fitted as readily as any other. `analytics_features.v1`
is now the same shape of contract as `developer_features.v1`, `learning_features.v1` and
`career_features.v1`, so a v2 cannot typecheck against v1 column meanings and a training row
cannot be attributed to the extraction that did not produce it.

**A repository that has never been scanned was published as a row of zeros.**
`developer_features.v1` now omits those repositories entirely. A row that exists means the
scan ran and found nothing, which is a measurement; an absent row means nobody has looked,
which the schema has no `available` column to say. Publishing zeros told a model "this
developer committed nothing" when the truth was "we have not looked". The same rule, on the
career side, was already correct: `career_features.v1.project_activity` is `null` for an
account with no repository ever scanned.

### What else landed

| Change | Where |
| --- | --- |
| `RateLimitMiddleware`: a fixed-window in-process limiter, 429 with the shared envelope and a `Retry-After` header, a separate budget shared by `/auth/login` and `/auth/password/forgot`, `OPTIONS` exempt, `X-Forwarded-For` off by default | §3, and `api-conventions.md` |
| Migration `0010_learning_career_integrity`: the career-evidence dedup rebuilt as a *partial* unique index with `NULLS NOT DISTINCT`, `learning_activities.skill_id` changed from `CASCADE` to `SET NULL`, and an index on the activity feed | §7 |
| Fifteen Phase 4 and Phase 6 settings documented, and the four productivity weights recorded as the start-up gate they are | §8, `development.md` §11.5 |

### What this document got wrong, and now does not

Stated plainly, because a corrected document that hides its own corrections is not a corrected
document:

- The backend table in §13 listed 55 test files and totalled 2090. The suite now collects
  **2270 across 67 files** (1029 offline, 1241 `integration`), and the twelve missing rows are
  the regression modules this pass added.
- §13 and `development.md` §10 said the frontend ran 607 tests in 42 files. `npm test` reports
  **645 tests in 44 files**, all passing — that one is a real pass count, run end to end.
- §13 said "runs all 2090 tests" as though a full pass had happened. It had not, on this
  machine, at the time it was written; the backend figures are **collection** counts and are
  now labelled that way. The last full run before this pass was 2136 passed / 9 failed.
- The chain was documented as nine revisions. It is ten; `0010` is the repair revision.
- [`api-conventions.md`](api-conventions.md) told readers that `Page[T]` was served by no
  endpoint, and that the frontend's `Paginated<T>` disagreed with it. Fifteen endpoints serve
  `Page[T]`, and the TypeScript type has nested its counters under `meta` for some time and
  is consumed by every knowledge, planner and work service. That sentence was the single most
  misleading claim in the documentation set.

`backend/tests/test_documentation_claims.py` now holds these documents to their own claims:
every `Settings` field documented in `.env.example` and the README, the `Page[T]` count
matching a live `app.openapi()`, the superseded figures gone, and `maintenance_activity` not
described as reading low when the shipped path reads at its ceiling.

### `maintenance_activity` reads at its ceiling, and three documents said it read low

Worth separating, because it was the widest-reaching error and it was **inverted**.

The metric answers "commits that reached a file this record had not seen touched recently",
with a 90-day lookback. Migration `0008` stores no per-commit file rows — it would grow to
millions on a mature codebase — so there is no file history to consult. The shipped path
therefore reports a single placeholder path
(`<file names are not stored per commit>`) for any commit that changed a file, and calls
`metrics.maintenance_activity` with `last_touched_before=None`. With no history supplied,
every touched file counts as quiet, so **every recorded commit that changed a file is
counted**: the metric's maximum. Its own explanation says so — *"measured without the
preceding 90 days of file history, so every touched file counts as quiet."*

`development.md` and both phase reports said it "reads low". That was wrong twice over. The
path before the repair would have reported a measured **zero** for every repository, which is
the one outcome this codebase may never produce; the path that replaced it reports the
ceiling. The only commits that do not count are those with `files_changed = 0`.

---

## 19. Phase 10 — ML training

Phase 10 is the first phase that trains a model, and it is structured to keep that fact
from touching the API. Everything it owns lives in `backend/ml/`, a sibling of `backend/app/`,
not a subpackage of it.

### 19.1 The boundary

| Rule | Why |
| --- | --- |
| Nothing under `backend/app/` imports `backend/ml/` | The API process must start on an interpreter that has no torch wheel, and it must not go down because a training import does |
| The data half is stdlib-only | `make ml-prepare`, `ml-validate` and `ml-eval` run on any interpreter, so the test suite stays fast and dependency-free |
| The training half needs its own interpreter | `backend/ml/.venv/` carries the torch wheel; `make` selects it through `ML_PY` |
| One entry point, three stages | `python -m ml.train --prepare / --train-small / --evaluate`; the `make ml-*` targets are thin wrappers over those flags. `--all` — or no flag at all — runs all three, always in that order |

The Makefile defines nine `ml-*` targets: `ml-help`, `ml-prepare`, `ml-datasets`,
`ml-validate`, `ml-train-small`, `ml-train-small-resume`, `ml-eval`, `ml-all` and
`ml-test`. Three wrap a stage flag one for one, and `--resume` is spelled
`ml-train-small-resume`. `ml-all` covers the whole pipeline, because the classifier
trains on CPU in about 66 minutes and there is no remote half to leave out.

### 19.2 One model, one place

| Model | Runs on | Purpose |
| --- | --- | --- |
| `microsoft/deberta-v3-base`, fine-tuned | this machine, CPU, `backend/ml/.venv` | routing / intent classification over the 14 Nexo intents |

The model's role is **routing, not answering**. It decides which capability should
handle an utterance, and twelve of the fourteen intents are handled by the deterministic
services in `app/services/`, which are faster and more reliable than any language model.
`out_of_scope` is trained so that abstaining is a class the model can be right about.
`code_assist` and `deep_reasoning` carry the destination `large-model:unavailable`: NEXUS
runs no language model, and those two classes are kept precisely so the router can
recognise a request it cannot serve rather than being blind to it. The classifier
therefore exists to keep the cheap intents cheap — it does not displace the rules.

Phase 10 ends at artifacts, and nothing in the application loads the model. Nothing
under `backend/app/` imports `backend/ml/`, no route reads a checkpoint, and the
deterministic path stays the answer whenever a model is absent, unevaluated or unsure.
Serving a trained model is later work — see the *ML training* row of §15 and
[`specifications/phase-10-architecture.md`](specifications/phase-10-architecture.md).

### 19.3 The honesty rules carry over

A training matrix makes a fabricated zero indistinguishable from an observed one, so the
`null`-not-`0` rule of §17.4 is a precondition here rather than a nicety: the corpora are
generated and counted in `backend/ml/reports/`, and each stage writes a manifest rather than a
claim. A stage that did not run is reported as not run.

---

## See also

| Document | Contents |
| --- | --- |
| [`../README.md`](../README.md) | Setup, quick start, environment variables, commands, troubleshooting, roadmap |
| [`api-conventions.md`](api-conventions.md) | Endpoint contract: versioning, error codes, request ids, pagination, the endpoint checklist |
| [`development.md`](development.md) | Clean-machine setup, daily workflow, adding an endpoint or a page, testing and style conventions |
| [`specifications/phase-8-developer-report.md`](specifications/phase-8-developer-report.md) | What Phase 8 shipped, the four git tables, and the two defects three agents independently reported |
| [`specifications/phase-9-learning-career-report.md`](specifications/phase-9-learning-career-report.md) | What Phase 9 shipped, the six learning/career tables, and the contract disagreements |
| [`specifications/phase-10-architecture.md`](specifications/phase-10-architecture.md) | Where the Phase 10 `backend/ml/` package sits, its two interpreters, and its execution boundary |
| [`specifications/phase-10-training.md`](specifications/phase-10-training.md) | The Phase 10 routing classifier: its corpus, validation, splits, training configuration and evaluation |