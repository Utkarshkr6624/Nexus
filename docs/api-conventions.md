# API conventions

The contract every NEXUS endpoint follows, and the rules a new endpoint must obey.
`README.md` covers how to *start* the stack; this document covers what the API
looks like once it is running.

**Status.** The conventions below are the contract the whole API follows, and they were
established by the Phase 2 identity slice — **16 paths and 17 operations** — which is
described in full in the [Endpoint catalogue](#endpoint-catalogue). Phases 3 through 9
added 170 further operations across 19 routers, and Phase 11 added 2 more across the
twentieth, for **142 paths and 189 operations** in total (`app.openapi()`, counted per path
and method). The catalogue has **not** been extended to cover them; their per-module
inventories live in the phase reports, and the conventions those phases established that
were not already written down are in
[Conventions from Phase 8 and Phase 9](#conventions-from-phase-8-and-phase-9) and
[Conventions from Phase 11](#conventions-from-phase-11-intent-routing). Three modules —
Search, AI Assistant and Experiments — still have no API at all.

The last remediation pass over Phases 1–9 changed three of those numbers and closed four
gaps; both are recorded in [Remediation pass over Phases 1–9](#remediation-pass-over-phases-19).

---

## Contents

- [Base URL and versioning](#base-url-and-versioning)
- [Endpoint catalogue](#endpoint-catalogue)
- [Request conventions](#request-conventions)
- [Response conventions](#response-conventions)
- [The error envelope](#the-error-envelope)
- [Error codes](#error-codes)
- [Validation errors](#validation-errors)
- [Request correlation (`X-Request-ID`)](#request-correlation-x-request-id)
- [Authentication](#authentication) | Tokens, sessions, the password policy, permissions, password reset |
- [CORS and response headers](#cors-and-response-headers)
- [Rate limiting](#rate-limiting) | The 429 envelope, the two credential routes, and why `X-Forwarded-For` is off by default
- [Pagination](#pagination)
- [Conventions from Phase 8 and Phase 9](#conventions-from-phase-8-and-phase-9) | Route order, page-size caps as rejections, partial PATCH, one-producer stamps, unmodifiable identity columns, null-not-zero |
- [Conventions from Phase 11](#conventions-from-phase-11-intent-routing) | The two intent-routing endpoints: what they return, what they refuse, and what a caller cannot do |
- [Health semantics](#health-semantics)
- [The client contract](#the-client-contract) | `ApiClient`, the services layer, the wire types, the codes only the client manufactures |
- [Checklist for a new endpoint](#checklist-for-a-new-endpoint) | The rules a new route must satisfy |
- [Worked example — the shape a future endpoint takes](#worked-example--the-shape-a-future-endpoint-takes)
- [Testing an endpoint](#testing-an-endpoint)
- [Remediation pass over Phases 1–9](#remediation-pass-over-phases-19) | What the audit found, what shipped, and which numbers in this document moved because of it
- [Known gaps](#known-gaps) | Deliberate omissions: what Phase 2 left open and what the later phases left open |

---

## Base URL and versioning

| Setting | Value | Where |
| --- | --- | --- |
| Version prefix | `/api/v1` | `settings.api_v1_prefix` in `backend/app/core/config.py` |
| Backend origin (local) | `http://127.0.0.1:8000` | `NEXUS_HOST` / `NEXUS_PORT`, read by `backend/run.py` |
| Browser-facing base | `VITE_API_BASE_URL` | `.env`; the code default is the relative `/api/v1` |

The prefix is mounted once, in `backend/app/main.py`:

```python
application.include_router(api_router, prefix=settings.api_v1_prefix)
```

Three paths deliberately sit **outside** the prefix: `GET /health` (liveness),
`GET /` (service metadata) and the documentation mounts. Liveness in particular
must not move under a version prefix — an orchestrator restart loop should not
have to be re-pointed when the API is versioned.

Rules:

- A breaking change to a response shape, a field meaning or an error code gets a
  **new prefix** (`/api/v2`), not a silent change to `/api/v1`.
- Adding an endpoint, an optional request field, an optional response field or a
  new error `code` is not breaking. Existing fields are never removed, renamed or
  retyped inside a version.
- New modules land under `/api/v1/<module>/…` — `/api/v1/projects`, then
  `/api/v1/tasks`. There is no `/api/v1/v1`.
- Version the *contract*, not the deployment. `APP_VERSION` in the health payload
  is the build version and moves with every release; it is not the API version.

Interactive documentation, when the server is running:

| Surface | URL |
| --- | --- |
| Swagger UI | `http://127.0.0.1:8000/docs` |
| ReDoc | `http://127.0.0.1:8000/redoc` |
| OpenAPI document | `http://127.0.0.1:8000/openapi.json` |

Start the server with `python run.py` **from `backend/`**. A bare
`uvicorn app.main:app` starts on Windows but cannot reach the database — see the
README's troubleshooting section.

---

## Endpoint catalogue

**The Phase 2 slice.** Health, auth and users — 17 operations across 16 paths, and the
part of the API this document inventories route by route. The API as a whole serves 142
paths and 189 operations; the other 172 operations belong to the modules Phases 3 through
9 added (projects, tasks, tags, activity, calendar, work sessions, planner, availability,
knowledge, analytics, developer, risks, recommendations, intelligence, learning, career)
and to Phase 11's intent router, and are catalogued in the phase reports. Everything here
still applies to every one of them unchanged.

### Unauthenticated

| Method | Path | Auth | Success | Notes |
| --- | --- | --- | --- | --- |
| `GET` | `/` | no | 200 | Service metadata and an endpoint index (`service`, `version`, `environment`, `status`, `api_version`, `api_prefix`, `links`) |
| `GET` | `/health` | no | 200 `{"status":"ok"}` | Liveness. Never touches the database |
| `GET` | `/api/v1/health` | no | 200, `status: "healthy"\|"degraded"` | Metadata plus a timed `SELECT 1` probe |
| `POST` | `/api/v1/auth/register` | no | 201, `UserRead` | 409 `conflict` if the email **or** the username is taken |
| `POST` | `/api/v1/auth/login` | no | 200, `TokenPair` | 401 `unauthorized` for any credential failure. Opens a device session and returns its `session_id`. 429 `rate_limited` once an address exhausts its credential budget — see [Rate limiting](#rate-limiting) |
| `POST` | `/api/v1/auth/refresh` | no | 200, `TokenPair` | Single-use rotation **on the same session row** — the device stays the same device |
| `POST` | `/api/v1/auth/password/forgot` | no | 202, `PasswordResetRequested` | The body is identical for a known and an unknown address. There is no 404. 429 `rate_limited` on the same credential budget as login |
| `POST` | `/api/v1/auth/password/reset` | no | 204, empty body | 401 for a token that is unknown, spent, expired or not a reset token — all four answer identically. Ends **every** session |
| `POST` | `/api/v1/auth/logout` | optional | 204, empty body | Revokes each token it is given. **Send the refresh token in the body** — the bearer header alone leaves it valid. See [Authentication](#authentication) |

### Bearer-authenticated

| Method | Path | Auth | Success | Notes |
| --- | --- | --- | --- | --- |
| `GET` | `/api/v1/auth/me` | bearer | 200, `UserRead` | 401 `unauthorized` for missing/invalid/expired/revoked tokens |
| `POST` | `/api/v1/auth/logout-all` | bearer | 204, empty body | Revokes every session **except** the one identified by the caller's own `sid` claim |
| `GET` | `/api/v1/auth/sessions` | bearer | 200, `SessionListRead` | The caller's live devices, newest first, each flagged `is_current` |
| `DELETE` | `/api/v1/auth/sessions/{session_id}` | bearer | 204, empty body | **404, never 403**, for a session the caller does not own. See [Ownership answers 404](#ownership-answers-404-not-403) |
| `PATCH` | `/api/v1/auth/password` | bearer | 204, empty body | 401 if `current_password` is wrong, 409 if `new_password` matches the current one. Ends every session except the caller's |
| `PATCH` | `/api/v1/users/me` | bearer + `users.write` | 200, `UserRead` | 409 when the requested username is taken. `email` and `password` are **not** editable here |
| `DELETE` | `/api/v1/users/me` | bearer | 204, empty body | Requires `{"password": "…", "confirm": true}`; 401 if the password does not verify. 422 if `confirm` is not `true` |
| `GET` | `/api/v1/users/` | bearer + `users.read` + admin role | 200, `UserRead[]` | **A permission-system fixture, not a product feature.** 403 for any caller whose role does not satisfy both gates |

### Ownership answers 404, not 403

`DELETE /api/v1/auth/sessions/{session_id}` scopes its lookup to the caller's own user id
(`SessionRepository.get_by_id_for_user`) and answers **404** when the id belongs to someone
else.

A 403 would be the wrong answer on this route and not for tidiness reasons: it would confirm
that the id exists, turning the endpoint into a probe for which session ids are real. A 404
is exactly what an id that never existed also returns, so the two cases are
indistinguishable from outside. The same reasoning applies to every future endpoint that
acts on a row the caller might not own — **404, not 403** — while a 403 stays correct for a
*capability* the caller simply does not have (see [Authentication](#authentication)).

Note the distinction the two cases make: `403` is for a capability the role map does not
grant (`GET /api/v1/users/`), and `404` is for a resource that is not the caller's to reach.

### The `User` shape

`UserRead` is the only user representation that leaves the process, and Phase 2 changed it:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Application-side UUIDv4 |
| `email` | string | Normalised to lower case before storage and comparison; unique |
| `username` | string | New in Phase 2. 3–32 characters, must start with a letter or a digit, then letters/digits/`_`/`-`. Unique, and **case-preserving** — trimmed but never folded, so the handle you sign in with is the handle you were shown |
| `display_name` | string \| null | Renamed from `full_name` in Phase 2. `full_name` no longer exists on the wire |
| `avatar_url` | string \| null | Optional. Must be an absolute `http`/`https` URL — anything else, `javascript:` above all, is a 422 |
| `role` | string | `"user"` or `"admin"`. Supersedes `is_superuser` |
| `permissions` | string[] | New in Phase 2. **Derived from `role` at serialisation time, never stored.** Sorted, so two responses diff cleanly. An unrecognised role yields `[]` |
| `is_active` | bool | An inactive account is rejected at authentication with 401 |
| `is_verified` | bool | Reserved. Phase 2 does not verify delivery, so nothing ever sets it |
| `created_at`, `updated_at` | datetime | |
| `last_login_at` | datetime \| null | Stamped on a successful login |

**`full_name` and `is_superuser` are gone from `UserRead`.** `full_name` was renamed;
`is_superuser` was superseded by `role`, and publishing both would invite a client to branch
on the one that is no longer consulted. The `is_superuser` *column* still exists on
`users` and is still honoured by the superuser dependency alongside `role`, so a Phase 1
administrator is not silently un-promoted; it is simply not something a client should read.

Branch on `permissions`, not on `role`. The list is the answer to "may I open this screen?",
and re-implementing the role → permission map in the client is exactly the drift the field
exists to prevent.

`hashed_password` is absent, as before, and no response body echoes a submitted password.

### Declared schema facts

Taken from the generated OpenAPI document:

- The security scheme is `HTTPBearer` (`type: http`, `scheme: bearer`,
  description "JWT access token"), so Swagger UI offers an **Authorize** button. In the
  Phase 2 slice every route except `/`, `/health`, `/api/v1/health`, `register`,
  `login`, `refresh`, `password/forgot` and `password/reset` carries
  `security: [{"HTTPBearer": []}]`; `logout`'s parameter is optional. Every module
  router added in Phases 3–9 requires the bearer scheme on all of its routes.
- **Error responses are not declared in the OpenAPI schema.** An operation lists
  only its 2xx (plus 422 where Pydantic validation applies). A 401/404/409 raised
  at runtime is documented here and in the Swagger description banner, not in the
  per-operation `responses`. See [Known gaps](#known-gaps).
- `POST /api/v1/users/` declares `security` for both the bearer scheme and the
  `require_permission` dependency, but the permission itself is not expressible in
  OpenAPI — a consumer reading the schema sees a bearer requirement, not a role one.

---

## Request conventions

- **Bodies are JSON.** `Content-Type: application/json`. Pydantic v2 parses and
  validates; a malformed body produces a 422 envelope, not a 500.
- **Field names are `snake_case`** on the wire in both directions — the Python
  models and the TypeScript types in `frontend/src/types/api.ts` use the same
  spelling. No aliases, no camelCase bridge.
- **Emails are normalised before validation**: trimmed and lower-cased by a
  `mode="before"` validator, and stored the same way. `  ADA@Nexus.DEV  ` and
  `ada@nexus.dev` are the same account, and uniqueness is checked on the
  normalised value.
- **Constraints are declared in the schema, not the service.** `email` is capped
  at 320 characters and matched against a deliberate shape regex (not RFC 5322 —
  `EmailStr` would need a dependency the backend does not ship); `username` at
  3–32 with a leading-alphanumeric pattern; `display_name` at 255; `avatar_url`
  at 2048 and restricted to absolute `http`/`https`. A new password is capped at
  128 and must satisfy the policy in [Authentication](#authentication) — a 422,
  not a 403. bcrypt only ever hashes the first 72 bytes of input, so
  `security._bcrypt_bytes` truncates before hashing; be aware of that ceiling
  when choosing a maximum password length.
- **The password policy is enforced by one validator on the type, not per field.**
  `Password` in `app/schemas/user.py` carries an `AfterValidator`, so
  registration, password change and password reset cannot drift apart. The rule
  ids (`min_length`, `uppercase`, `lowercase`, `digit`, `special`) and their order
  are a shared vocabulary that `password_rule_status()` returns for the client's
  live checklist.
- **Secrets are write-only.** `hashed_password` is absent from every model that
  can leave the process, and no response body may echo the submitted password
  (`backend/tests/test_auth.py` asserts this).
- **Query parameters** are scalars (`string`, `int`, `bool`, `null`). Nested
  structures go in the body. Unknown query parameters are ignored, not rejected.
- **Send `Accept: application/json`** (the client does this by default).
- **No cookie auth.** `Authorization: Bearer …` is the only credential; there is
  no session cookie and no CSRF surface.

---

## Response conventions

- **2xx bodies are the resource itself** — no `{"data": …}` wrapper on success.
  The wrapper exists only for errors, which keeps the success path trivially
  typed on the client.
- **201 for creation**, with the created resource as the body and no `Location`
  header requirement. **204 with a zero-length body** for a delete or a
  successful action that has nothing to return; the client treats an empty 204
  as `undefined`, never as an empty object.
- **Identifiers are UUID v4 strings**, generated application-side (see
  `UUIDPrimaryKeyMixin` in `backend/app/db/base.py`) so an id is known before the
  row is flushed and no row count is leaked.
- **Timestamps are ISO-8601 UTC.** Pydantic serialises `DateTime(timezone=True)`
  columns as ISO-8601; the hand-built health `timestamp` is
  `2026-01-01T00:00:00.000Z` (millisecond precision, `Z` suffix). The
  TypeScript alias is `ISODateTimeString = string` — the shape is a convention,
  not a compile-time guarantee.
- **A field that has no value is `null`, never absent and never `""`.** Absent is
  reserved for "not applicable to this representation".
- **One model per representation.** `UserRead` is the only user shape that leaves
  the process; the ORM model never is.

---

## The error envelope

Every non-2xx response has exactly this body, with exactly these four keys:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request body or query parameters failed validation.",
    "details": {
      "errors": [
        {
          "field": "password",
          "message": "String should have at least 8 characters",
          "type": "string_too_short",
          "context": { "min_length": "8" }
        }
      ]
    },
    "request_id": "1f0a1d0c-6f2a-4a1e-9b0f-6f4c1d2e3a4b"
  }
}
```

| Field | Type | Contract |
| --- | --- | --- |
| `code` | string | Stable `snake_case` identifier. **Branch on this.** Never on `message` |
| `message` | string | Always present, always non-empty, always safe to render to a user. No stack trace, no SQL, no driver or module name, no credentials |
| `details` | object \| null | Machine-only structured context. `null` when there is nothing to add. Never rendered verbatim |
| `request_id` | string | The same value as the `X-Request-ID` response header. **Present on every response, including a 5xx** — the two are stamped from one source and never disagree |

Rules that are enforced by tests in `backend/tests/test_errors.py`:

- The top-level payload has exactly one key, `error`.
- `code` is lowercase `snake_case` and alphanumeric.
- A response body may not contain `traceback`, `psycopg`, `sqlalchemy`,
  `asyncpg`, `alembic`, a file path fragment, a SQL keyword, a column reference
  or an internal package path.
- An unhandled exception is logged server-side with its traceback and answered
  with `internal_error` / "An internal server error occurred." — the client learns
  nothing but the `request_id` needed to find the log line.

### 5xx is deliberately opaque

For **any** status `>= 500` — the catch-all handler *and* an
`HTTPException`/`StarletteHTTPException` that carries a 5xx — the envelope is
fixed:

| Field | 5xx value |
| --- | --- |
| `code` | `internal_error` (a 5xx always maps there; see [Error codes](#error-codes)) |
| `message` | The single constant `"An internal server error occurred."` (`_INTERNAL_ERROR_MESSAGE` in `backend/app/core/exceptions.py`) |
| `details` | Always `null` |
| `request_id` | The correlation id — the only thing that survives |

The originating exception's own text is logged (`unhandled_exception` carries the
exception type; `http_exception` carries `detail`) and is reachable by
`request_id` alone. It is never echoed, because that text is text NEXUS did not
write — a framework or dependency string, or application text about a failure —
and it can carry SQL, module paths or credentials.

**A handler must not rely on a 5xx `detail` reaching the client.** If you raise
`HTTPException(500, "cannot reach ledger shard 3")` from a service, the user sees
`"An internal server error occurred."` and a developer sees your sentence only in
the log. Passing `detail` to a 5xx is therefore a private logging channel at
best; do not build user-facing behaviour on it. Raising a domain error with a
5xx is not a way around this either — the same constant is used for every 5xx.

Raise the errors from the **service** layer, never from a router and never by
returning an error-shaped `JSONResponse` by hand:

```python
from app.core.exceptions import ConflictError, NotFoundError, UnauthorizedError

if user is None:
    raise NotFoundError("User not found.")
```

`install_exception_handlers` in `backend/app/core/exceptions.py` turns
`NexusError`, `RequestValidationError`, `StarletteHTTPException` and bare
`Exception` into the envelope, so a new error type only needs a `code`, a
`status_code` and a `default_message`.

---

## Error codes

Declared in `ErrorCode` (`backend/app/core/exceptions.py`) and mapped to HTTP
status by `_STATUS_CODE_TO_ERROR_CODE`. That table covers eight statuses; the
rest resolve through `_status_code_to_code`:

| `code` | Status | Raised by | Typical cause |
| --- | --- | --- | --- |
| `validation_error` | 422 | `RequestValidationError` handler; the `ValidationError` domain class | Body/query failed schema validation |
| `not_found` | 404 | `NotFoundError`, and any unmatched path | Unknown route, or a resource id that does not exist |
| `unauthorized` | 401 | `UnauthorizedError` | Missing, malformed, expired, wrong-type or revoked token; wrong credentials; inactive account |
| `forbidden` | 403 | `ForbiddenError` | Authenticated but not permitted: the caller's role does not grant the required `Permission`, or it is not the administrator role |
| `conflict` | 409 | `ConflictError` | Uniqueness violation, or a collision with current state |
| `bad_request` | 400 | Fallback for any unmapped 4xx; reserved as a domain code | Malformed request that is not schema-invalid |
| `method_not_allowed` | 405 | `StarletteHTTPException` | Wrong verb on a known path |
| `rate_limited` | 429 | `RateLimitMiddleware` | The client address exceeded its budget for this route inside the current window. See [Rate limiting](#rate-limiting) |
| `ml_unavailable` | 503 | `MLUnavailableError` and its subclasses | ML is switched off, the trained checkpoint is not on disk, `torch` is missing, or a load failed. See [Conventions from Phase 11](#conventions-from-phase-11-intent-routing) |
| `internal_error` | 500 | `NexusError` default, `StarletteHTTPException` 5xx, the catch-all handler | Unexpected failure; the client is told nothing |

The code table is unchanged by Phase 2 — and by Phases 3 through 9, which added 170
operations without needing a tenth code. The remediation pass did not add one either:
`rate_limited` was already in the enum, and the limiter finally produces it. **Phase 11
did add one**, `ml_unavailable`, and it is worth saying why that took nine phases of
deferring: a deployment with no classifier is a normal state, not an error, and folding it
into `internal_error` would have sent every client down its "something is broken on our
side" path for a feature an operator can switch back on with one environment variable. A
503 with its own code is the only answer that tells a client *retry later* rather than
*stop asking us*. What changed in the earlier phases is **which situations produce the
existing codes**, and one of them is a rule worth stating on its own:

- **A resource the caller does not own is `not_found` (404), never `forbidden` (403).**
  `DELETE /auth/sessions/{session_id}` answers 404 when the id belongs to another account,
  because a 403 would confirm that the id exists and turn the route into a probe for real
  session ids. 403 is reserved for a *capability* the role map does not grant. A new
  endpoint that acts on a caller-scoped row must follow the same rule; see
  [Ownership answers 404](#ownership-answers-404-not-403).

New 4xx situations introduced in Phase 2, all reusing the existing codes:

| Situation | Code | Status | Raised by |
| --- | --- | --- | --- |
| Username already registered, or `PATCH /users/me` picks a taken one | `conflict` | 409 | `AuthService.register`, `UserService.update` |
| Password fails the policy (on register, change or reset) | `validation_error` | 422 | the `AfterValidator` on `Password` — a schema failure, not an authorisation one |
| `new_password` equal to `current_password` on change | `conflict` | 409 | `AuthService.change_password` |
| `confirm` absent or false on `DELETE /users/me` | `validation_error` | 422 | the `confirm` field validator on `UserDeletion` |
| `avatar_url` that is not an absolute `http`/`https` URL | `validation_error` | 422 | the `avatar_url` before-validator on `UserUpdate` |
| Reset token unknown, spent, expired or wrong-type | `unauthorized` | 401 | `AuthService.complete_password_reset` — one message for all four, deliberately |
| Session that is not the caller's, or does not exist | `not_found` | 404 | `SessionService.revoke` |
| Role does not grant the route's permission | `forbidden` | 403 | `require_permission()` |
| Wrong `current_password`, or a wrong password on account deletion | `unauthorized` | 401 | `AuthService.change_password`, `UserService.delete_account` |

The two rules that are easy to get backwards:

- **An unmapped 4xx becomes `bad_request`, never `internal_error`.** A status
  outside the table — 413 (payload too large), 415 (unsupported media type),
  402, 418, anything — resolves to `bad_request`. Reporting a caller's mistake
  as `internal_error` would blame the server for a failure the server did not
  cause, and would send the caller looking in the wrong logs.
- **`internal_error` is reserved for 5xx.** `_status_code_to_code` only returns
  it when `status_code >= 500`. No 4xx can ever carry it.

So if an endpoint needs an exact code/status pair, raise a domain error with the
`code` and `status_code` it sets on the class — do not reach for `HTTPException`,
whose code is derived from the status and may be the fallback.

Also attached to the response:

- A **401 from a domain error** carries `WWW-Authenticate: Bearer`.
- A 405 carries Starlette's `Allow` header.
- Framework-generated messages (for example the 404 body is
  `{"code":"not_found","message":"Not Found",…}`) are not curated. They are still
  user-safe; do not treat them as stable copy. The 5xx message *is* fixed, per
  [5xx is deliberately opaque](#5xx-is-deliberately-opaque).

### Client-only codes

The frontend client manufactures a few codes that never come from the server. They
exist so UI code has one error shape to branch on, and they always carry
`status: 0` (no HTTP response was received):

| `code` | `status` | Meaning |
| --- | --- | --- |
| `timeout` | 0 | The client aborted the request after its 30 s default |
| `network_error` | 0 | `fetch` rejected — the server is not listening, or DNS/TLS failed |
| `aborted` | 0 | The caller cancelled (React Query unmount) |
| `invalid_response` | *n* | A 2xx body that did not parse as JSON |
| `unknown_error` | 0 | Anything thrown that is not already an `ApiError` |

If the response body is not the envelope, `ApiClient` falls back to a
status-derived code (400 → `validation_error`, 404 → `not_found`, …) and keeps
the body text as the message, truncated to 500 characters. Do not write UI that
depends on that path: it is a safety net for proxies and misrouted requests.
Note that `STATUS_CODE_FALLBACK` in `api-client.ts` and `_status_code_to_code`
in `backend/app/core/exceptions.py` are deliberately not the same table: they
agree on 401, 403, 404, 409, 422 and 429, but on 400 the client says
`validation_error` and the server says `bad_request`. The two only ever meet on
a response that is already outside the contract.

---

## Validation errors

A 422 always has `details.errors`, a list. Each entry:

| Key | Always present | Meaning |
| --- | --- | --- |
| `field` | yes | Dotted path to the offending field, with the `body` prefix removed. A non-field error is reported as `"body"` |
| `message` | yes | Human-readable description of this one failure |
| `type` | yes | Pydantic error kind, e.g. `string_too_short`, `string_pattern_mismatch`, `missing` |
| `context` | no | Extra Pydantic context, stringified so it stays JSON-safe. The raw `error` key is dropped |

Every entry is a single problem; a payload with two bad fields yields two entries,
and `message` stays the generic sentence. Map `details.errors[].field` onto form
inputs and render `details.errors[].message` next to them.

This contract is load-bearing, not aspirational: `login-page.tsx` and
`register-page.tsx` in `frontend/src` both flatten `details.errors[]` into
`{field: message}` and render the result under the `email` and `password`
inputs. Both drop entries whose `field` is `"body"` (not field-scoped) and keep
the first message when a field repeats. So a new endpoint that returns a 422
should keep the dotted-path `field` aligned with its own request body, and a new
form should follow that page's flattening rather than re-deriving paths from the
top-level `message`.

Do not parse `message` of the envelope for field information — it carries none.
`ApiError.fieldErrors` in `frontend/src/lib/api-client.ts` returns `details`, which
is the whole `{"errors": […]}` object.

---

## Request correlation (`X-Request-ID`)

`RequestContextMiddleware` in `backend/app/core/middleware.py` is the
**outermost** layer — above Starlette's `ServerErrorMiddleware`, not merely the
outermost user middleware. It cannot be installed with `app.add_middleware`,
which can only insert *inside* `ServerErrorMiddleware`; `add_request_context_middleware`
overrides `build_middleware_stack` (via `_install_outermost`) so the layer wraps
the entire stack, and defers the build to the first request so handlers and
routes registered afterwards are still included. It:

1. Resolves a correlation id, in priority order `X-Request-ID`,
   `X-Correlation-ID`, `X-Trace-ID`, else a fresh `uuid4()`. An inbound value is
   attacker-controlled, so it is stripped and truncated to 128 characters.
2. Binds it to a contextvar, so **every** log line emitted while handling the
   request carries it.
3. Stamps `X-Request-ID` onto the outgoing `http.response.start` ASGI message —
   so it is attached to whatever response is produced, including the 500 that
   `ServerErrorMiddleware` renders *below* it.
4. Reads the status back off that same message (defaulting to 500 if no response
   ever started) and emits exactly one `request_completed` access line with
   method, path, status, `duration_ms`, client IP, query and user agent.

Rules for callers:

- Send `X-Request-ID` (or `X-Correlation-ID`) to have your own id adopted, then
  quote the echoed value in a bug report.
- The header is safe to read cross-origin: it is listed in CORS `expose_headers`.
- **The header is present on every response, including a 5xx, and always equals
  the body's `request_id`.** Because the layer sits above `ServerErrorMiddleware`,
  it observes the failure response too; a client can correlate a 500 from the
  header alone, without having to parse the body. (`resolve_request_id` in
  `backend/app/core/exceptions.py` reads the contextvar first and falls back to
  the id stashed on `request.state`, because the contextvar is unbound by the
  time the catch-all handler runs.)
- The field is also a key in the JSON log lines, so
  `docker compose logs backend | grep <id>` finds the whole request.

Corollary for anyone adding middleware: a new layer added with
`app.add_middleware` sits *inside* `ServerErrorMiddleware` and therefore cannot
stamp a header onto a 500. If a layer must observe unhandled failures, it has to
be installed the way `add_request_context_middleware` does it.

Log levels follow the outcome: 5xx → ERROR, 4xx or `duration_ms >=
SLOW_REQUEST_MS` (default 1000) → WARNING, otherwise INFO. `LOG_REQUEST_BODY=true`
adds a redacted, 2000-character-truncated body preview to the access line; leave
it off outside debugging.

---

## Authentication

Bearer JWT, HS256, signed with `SECRET_KEY`. Issued by
`app/core/security.py`, policed by `app/services/auth_service.py`.

### Token shape

| Claim | Meaning |
| --- | --- |
| `sub` | The user id (UUID string) |
| `type` | `access` or `refresh` — the claim that stops a refresh token being replayed as a bearer credential |
| `iat` / `nbf` | Issued-at / not-before, UTC |
| `exp` | Expiry, UTC |
| `jti` | Token id; the key the access-token revocation denylist is built on |
| `sid` | The device session this token belongs to, UUID string. **New in Phase 2.** Required on a refresh token; `None` on an access token minted outside the session flow |

`exp`, `sub` and `type` are required on decode. A token missing any of them is
rejected, not tolerated. `sub`/`type`/`exp` are owned by the security module and
cannot be overridden by caller-supplied claims.

| Token | Lifetime | Source |
| --- | --- | --- |
| Access | 60 minutes | `ACCESS_TOKEN_EXPIRE_MINUTES` |
| Refresh | 7 days | `REFRESH_TOKEN_EXPIRE_DAYS` |
| Session row | 30 days, absolute | `SESSION_ABSOLUTE_LIFETIME_DAYS` |
| Reset token | 30 minutes | `PASSWORD_RESET_EXPIRE_MINUTES` |

The `TokenPair` body is `{access_token, refresh_token, token_type: "bearer",
expires_in, session_id}` where `expires_in` is the access lifetime **in seconds**.
The refresh lifetime is not in the body; derive the refresh deadline from
`REFRESH_TOKEN_EXPIRE_DAYS`. `session_id` is the device the pair belongs to —
new in Phase 2, and nullable only so a token minted outside the session flow
still validates. Every real issuance path sets it.

### Flow rules

- **Login** returns a pair for an active account and creates the device session
  before the pair is returned. Any credential failure returns the identical
  message — "Incorrect email or password." — and takes comparable time, so probing
  cannot distinguish an unknown address from a wrong password.
- **Refresh is single-use.** The row's `token_hash` is replaced with the new
  token's digest before the new pair is issued, so a replay finds a digest that no
  longer matches and fails with 401 `unauthorized`.
- **Type confusion is an error.** An access token presented to `/auth/refresh`
  (or a refresh token to `/auth/me`) is 401 `unauthorized` with a message naming
  the required type.
- **A refresh token without a `sid` claim is rejected.** A token minted before
  sessions existed cannot be revoked individually or attributed to a device, and
  accepting one would leave exactly the gap sessions were introduced to close.
- **Logout revokes what it is given — so a client must send the refresh token.**
  `POST /auth/logout` takes two independent, both-optional inputs: a
  `TokenRefresh` body (`{"refresh_token": "…"}`) and an optional
  `Authorization: Bearer …` header. Anything it does not receive stays valid.
  Sending neither is legal and revokes nothing.
- The response is 204 with an empty body whenever the request itself is
  well-formed. A token *string* that is unparseable, already expired or already
  revoked is ignored, not reported — the service swallows every failure, so logout
  never fails the caller. A malformed *body* is still schema-validated like any
  other, and is a 422.
- **Changing a password keeps the caller signed in.** `PATCH /auth/password`
  exempts the session named by the caller's own `sid` and ends every other one. A
  password change is only half a control unless the sessions signed in with the old
  one are closed too.
- **Passwords are bcrypt, cost 12**, salted per hash; a corrupt or missing stored
  hash fails the check rather than raising, so one bad row cannot turn a login
  into a 500.
- The frontend calls logout best-effort and clears local state regardless, so a
  failed logout never traps the user in a signed-in shell. It presents **both**
  tokens — `logoutRequest` in `frontend/src/services/auth.ts` puts the refresh
  token in the body and passes the access token as an explicit `Authorization`
  header, and sends `{ auth: false }` so an already-expired access token cannot
  trip the client's 401 recovery and rotate the very refresh token the call is
  revoking.

### Sessions

A refresh token is backed by a row in `sessions`: one row per browser or device,
carrying a SHA-256 `token_hash` (never the raw token), the user agent, the client
address, an absolute expiry, and `last_used_at` / `revoked_at`.

| Rule | Consequence on the wire |
| --- | --- |
| Rotation **replaces** the row's `token_hash` rather than adding a row | Rotating a hundred times is still one device in `GET /auth/sessions`, and signing it out is one update |
| A replayed (rotated-away) refresh token fails | 401, identical to every other session rejection — the caller cannot tell "signed out" from "already rotated" from "not yours" |
| `MAX_ACTIVE_SESSIONS` (20) evicts the **oldest** live rows on sign-in | Sign-in beyond the cap still succeeds; the oldest device is signed out. A stolen refresh token cannot be replayed to accumulate access |
| `SessionRead` omits `token_hash` and `user_id` | Nothing a caller needs, and no credential material in a list response |

### Password policy

A new password must be at least `PASSWORD_MIN_LENGTH` characters (default 8) and
contain an uppercase letter, a lowercase letter, a digit, and one character that is
neither a letter nor a digit — a space counts as the last of those. Enforced on
register, on change and on reset by one validator, so a client cannot reach two
different answers.

A failure is a **422 `validation_error`** with `details.errors[].field` naming the
password field, not a 403: the payload was schema-invalid.

> **Known one-way divergence.** The browser's digit check is the Unicode property
> escape `\p{Nd}` (decimal digits), while Python's `str.isdigit()` also accepts
> other numeric categories — `²`, for instance. The client is therefore slightly
> **stricter** than the server for exotic characters: a password containing `²` but
> no ASCII digit is shown as failing and would in fact have been accepted. The
> reverse never happens — nothing the checklist accepts is rejected server-side.
> This is a UX affordance, never an authorisation decision; the server remains the
> only authority.

### 401 versus 403

401 means "who are you is unknown or unproven" — absent header, bad signature,
expired, wrong type, unknown subject, inactive account, revoked token. 403 means
"known, not permitted" — the caller's role does not grant the required
`Permission`. **A row the caller does not own is 404, not 403**; see
[Ownership answers 404](#ownership-answers-404-not-403). Every 401 from a domain
error carries `WWW-Authenticate: Bearer`.

### Permissions

`app/core/permissions.py` defines a `Permission` StrEnum of **eleven** capabilities —
`users.read`, `users.write`, `projects.read`, `projects.write`, `tasks.read`, `tasks.write`,
`analytics.read`, `calendar.read`, `calendar.write`, `knowledge.read`, `knowledge.write` —
a `ROLE_PERMISSIONS` map, and a `require_permission()` dependency factory. A protected
route declares the capability it is guarding:

```python
@router.patch(
    "/me",
    response_model=UserRead,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
```

Two properties are worth relying on. **It fails closed**: an unrecognised role
gets an empty permission set and the request is denied, because that value is
drifted *data* and a 500 on every protected endpoint for every user is worse than
a denial. And **it runs after authentication, not instead of it**, so an anonymous
request cannot tell "you are not signed in" from "you may not do that".

`permissions` on `UserRead` is derived from this same map, so a client never has
to re-implement it.

### Password reset

NEXUS is local-first and ships no mail service, so the reset token is returned in
the response body instead of being emailed.

| Property | Rule |
| --- | --- |
| `POST /auth/password/forgot` | **202** with `{"accepted": true, "dev_token": …}`. The body is byte-for-byte identical for a registered and an unregistered address, so the endpoint cannot enumerate accounts. The *only* difference is whether `dev_token` carries a token — which itself discloses the same fact, and is why `dev_token` is `null` whenever `ENVIRONMENT=production` |
| `dev_token` | The raw reset token, **non-production only**. It is a bearer credential for a full account takeover. The field exists in the schema so the OpenAPI shape does not change between environments, not because it is safe to send anywhere |
| `POST /auth/password/reset` | 204. Unknown, spent, expired and wrong-type tokens all answer with the identical 401 |
| Sessions | A successful reset ends **every** session, including ones that existed when the reset was requested. This is the recovery path for a compromised account; leaving one behind would leave the compromise in place behind a new password |

Password reset tokens are stored the same way refresh tokens are: a SHA-256 digest,
compared with `hmac.compare_digest`.

`POST /auth/password/forgot` also shares the **credential rate-limit budget** with
`/auth/login` — 120 requests per client address per 60-second window by default. An
earlier revision of this document listed the endpoint's lack of throttling as a known gap; it
is throttled now, and the identical-body rule above is the second of the two defences rather
than the only one. Neither defence is a substitute for the other: an identical body stops a
caller from *learning* which addresses exist, and the budget stops them from *trying* many.

**Why SHA-256 and not bcrypt for tokens.** `hash_token()` in
`app/core/security.py` digests refresh and reset tokens with SHA-256 rather than
the bcrypt used for passwords, and the reason is the *input*, not the algorithm.
A password is low-entropy, human-chosen and guessable, so it needs a deliberately
expensive work factor to make offline attack cost anything. A refresh or reset
token is a 256-bit cryptographically random signed value: there is no dictionary,
so no work factor buys anything, and a slow hash would cost ~250 ms on every
request that touches a session row. The raw token is never persisted and never
logged, so a database leak yields digests that cannot be replayed — an attacker
would have to recover the preimage of a 256-bit value. `token_fingerprint_matches`
compares with `hmac.compare_digest` rather than `==`, because an ordinary string
comparison short-circuits on the first differing byte and its timing is a weak but
free-to-remove oracle.

### Revocation store, and its limits

There are **two** mechanisms, and they do different jobs.

**Access tokens** are denylisted in memory. `RevocationStore` is an in-process
`dict` keyed by `jti`, purged on lookup, expiring entries when the token would have
expired anyway. Consequences to design around:

- A restart clears it. An access-token logout is not durable across a process
  restart.
- It is not shared between processes. The stack runs one backend worker, so this
  is correct today; a second worker needs Redis (the interface is deliberately
  narrow for exactly that).

**Refresh tokens and sessions** are database-backed, so they *are* durable and
restart-safe. `sessions.revoked_at` is the record, `POST /auth/logout-all` and
`PATCH /auth/password` are the bulk operations, and `DELETE /auth/sessions/{id}`
is the per-device one. An access token whose session has been revoked is still
cryptographically valid until it expires, but any refresh attempt against the dead
session is refused — so the window is bounded by `ACCESS_TOKEN_EXPIRE_MINUTES`, not
by `REFRESH_TOKEN_EXPIRE_DAYS`.

---

## CORS and response headers

Configured in `backend/app/main.py` from `CORS_ORIGINS` (comma-separated, no
trailing slashes; default `http://localhost:5173,http://127.0.0.1:5173`):

| Setting | Value |
| --- | --- |
| `allow_origins` | Parsed from `CORS_ORIGINS`; empty list disables the middleware entirely |
| `allow_credentials` | `True` |
| `allow_methods` / `allow_headers` | `*` |
| `expose_headers` | `["X-Request-ID"]` |

There is one other custom response header, and only on throttled requests:
`Retry-After`, in whole seconds, on a 429. See
[Rate limiting](#rate-limiting).

### Same-origin by default

The Vite dev server (`:5173`) and `vite preview` (`:4173`) proxy `/api` and
`/health` to the backend, so the browser can call the API same-origin and CORS
never enters the picture. The proxy target is `VITE_DEV_PROXY_TARGET`, default
`http://localhost:8000`, set to `http://backend:8000` by Docker Compose.

The shipped `.env` sets `VITE_API_BASE_URL=http://localhost:8000/api/v1`, which
makes the client call the backend cross-origin instead and exercises the CORS
path. Either works; set the variable to `/api/v1` to go through the proxy.

---

## Rate limiting

`RateLimitMiddleware` in `backend/app/core/middleware.py` throttles by client
address and route, in a fixed window, held in process memory. It is the innermost
user middleware, installed **inside** CORS so that a browser can read the 429 and
its `Retry-After` rather than seeing an opaque CORS failure.

This section did not exist before the final remediation pass. The document used
to carry a "known gap" saying there was **no** rate limiter and that the
`rate_limited` error code was therefore unreachable. That was true when it was
written and is false now: the limiter exists, `429` is reachable, and the
`rate_limited` code is the one it carries.

| Property | Value |
| --- | --- |
| Enabled | `RATE_LIMIT_ENABLED` (default `true`) |
| Window | `RATE_LIMIT_WINDOW_SECONDS` (default `60`), fixed, not sliding |
| Bucket key | client address and the **concrete** request path. The two credential routes share one bucket; every other route gets its own |
| General budget | `RATE_LIMIT_GENERAL_MAX_REQUESTS` (default `600`) per path per address |
| Credential budget | `RATE_LIMIT_CREDENTIAL_MAX_REQUESTS` (default `120`) for `/auth/login` and `/auth/password/forgot`, counted together |
| Exempt | `OPTIONS`, which is a preflight and reaches no handler |
| Unknown address | Requests with no client address share one `unknown` bucket |
| Store ceiling | `RATE_LIMIT_MAX_ENTRIES` (default `10000`) tracked pairs; past that, the oldest-inserted key is evicted |
| Sweeping | Each entry is dropped once its window has elapsed, and the sweep runs at most once per window rather than per request |

A throttled request answers **429** with the shared error envelope,
`code: "rate_limited"`, and a **`Retry-After`** header in whole seconds, rounded **up** and
never below `1`. It is still correlated and still access-logged: the limiter sits below
`RequestContextMiddleware`, so the 429 carries an `X-Request-ID` and leaves the usual
`rate_limited` WARNING line with the path, the client address and which budget refused it.

Five properties a client should know:

- **A refused request is not counted.** Counting it would extend the penalty past the window
  that caused it: a caller who kept hammering would never see the budget return, and the
  `Retry-After` it was handed would be a lie.
- **The window is fixed, not sliding.** `Retry-After` is the remainder of the window that is
  already running, not a rolling cooldown, so an immediate retry may still be refused.
- **The limiter runs before the handler.** The 429 body is therefore identical whether or not
  the account exists, the password was right or the caller was authorised — a caller cannot
  tell "no such account" from "wrong password" from "you may not ask again this minute".
- **General buckets are keyed on the concrete path.** An enumerator varying the last segment
  (`/projects/1`, `/projects/2`, …) draws a fresh budget each time. That is acceptable here
  because the generous bucket is the one that allows it; the two routes the limiter exists
  for are fixed paths by construction.
- **The store is per process.** A restart clears every counter, and N workers enforce N
  budgets. See [Known gaps](#known-gaps).

The two credential routes are the reason the limiter exists. Both answer an
unauthenticated caller with exactly what an attacker wants to know, and bcrypt costs about
a quarter of a second a try — which slows a guess down but never stops it. 120 a minute is
two a second, below the ~4/s one verification already permits, so the limiter does not extend
a patient attacker's timeline; it stops a *parallelised* flood and the address rotation an
enumerator would otherwise use. They are counted **together**, so an attacker cannot spend
the login budget and then try the reset route for free.

> `RATE_LIMIT_TRUST_FORWARDED_FOR` is off by default, deliberately: `X-Forwarded-For`
> is attacker-controlled on any path that does not terminate in a proxy you control, so
> honouring it would let a caller mint a fresh bucket per request. Turn it on only behind
> a trusted reverse proxy — and then every client shares the proxy's address, which is the
> correct reading in that topology and a denial-of-service waiting to happen in any other.

---

## Pagination

`Page[T]` and `PageMeta` in `backend/app/schemas/common.py` are the reserved
envelope for the list endpoints.

> **Two shapes are in use, and the field names are the same in both — only the nesting
> differs.**
>
> - **`Page[T]`, the `meta`-nested shape below, is served by fifteen operations** across
>   the `analytics`, `calendar`, `knowledge`, `projects`, `tags`, `tasks` and
>   `work_sessions` routers.
> - **A flat typed list response — `items`, `total`, `limit`, `offset` and a tally
>   beside them (`by_status`, `by_type`, `by_kind`, `by_level_source`) — is served by the
>   Phases 7–9 routers** (`developer`, `learning`, `career`, `risks`, `recommendations`).
>   Their schemas are named after the tally, not after `Page`, and they carry no `meta`
>   key.
>
> A client written against one shape will silently read `undefined` out of the other, so
> check which shape the route returns before consuming it. The rules below govern both.
>
> **An earlier revision of this document carried a "known gap" claiming that `Page[T]`
> was declared but unserved and that no endpoint spoke it yet. Fifteen do.** That claim
> was the most misleading sentence in the file, and the
> [Remediation pass](#remediation-pass-over-phases-19) removed it.
> `backend/tests/test_documentation_claims.py` fails the build if it returns, and checks
> the "fifteen" against a live count taken from `app.openapi()`.

`GET /api/v1/users/` does return a bare array, precisely because it is a
permission fixture and not a product surface.

```json
{
  "items": [],
  "meta": { "total": 0, "limit": 50, "offset": 0 }
}
```

| Field | Rule |
| --- | --- |
| `items` | The page's rows, in a deterministic order |
| `meta.total` | Total rows matching the filter, `>= 0` — the count without the page applied |
| `meta.limit` | Page size actually applied, `>= 1` |
| `meta.offset` | Rows skipped, `>= 0` |

Rules for the endpoints that adopt it:

- Parameters are `limit` and `offset`; the count always reflects the filters, not
  the page. Keep `limit >= 1` and cap it server-side — never honour an unbounded
  `limit` straight from the client.
- Always order by something unique, with a stable tiebreaker, or `total` and
  pagination will disagree with the rows returned.
- Filtering, sorting and search arrive as additional query parameters; keep the
  pagination parameters themselves fixed.
- Return the envelope directly — do not wrap it again.

`Message` (a `{"message": "…"}` acknowledgement body) is declared in the same
module for endpoints that acknowledge an action and have nothing else to return.
It is still unused: every action-only endpoint in the catalogue — `logout`,
`logout-all`, session revoke, password change, password reset, account deletion —
returns `204` instead, which is the stronger signal. Prefer `204`.

---

## Conventions from Phase 8 and Phase 9

Phases 3–9 added 170 operations that are **not** in the catalogue above, which was last
revised for Phase 2, and Phase 11 added two more. Rather than restate a catalogue that is
already behind, this section records the conventions those phases established that are *not*
already written down somewhere in this document. Everything else — the envelope, the code
table, `404` over `403` — still applies unchanged.

| Subsystem | Routes | Report |
| --- | --- | --- |
| Projects, tasks, tags, activity (Phase 3) | `/projects/*`, `/tasks/*`, `/tags/*`, `/activity/*` | — |
| Planner, calendar, work sessions, availability (Phase 4) | `/planner/*`, `/calendar/*`, `/work-sessions/*`, `/availability/*` | — |
| Knowledge (Phase 5) | `/knowledge/*` | — |
| Analytics (Phase 6) | `/analytics/*` | — |
| Risk and recommendations (Phase 7) | `/risks/*`, `/recommendations/*`, `/intelligence/*` | [`phase-7-report.md`](specifications/phase-7-report.md) |
| Developer (Phase 8) | 14 under `/developer` | [`phase-8-developer-report.md`](specifications/phase-8-developer-report.md) |
| Learning and career (Phase 9) | 19 under `/learning`, 12 under `/career` | [`phase-9-learning-career-report.md`](specifications/phase-9-learning-career-report.md) |
| Intent routing (Phase 11) | `/ml/status`, `/ml/route` | [`phase-11-report.md`](specifications/phase-11-report.md) |

Operation counts for every row — including the two Phase 8 and 9 figures, which the phase
reports state as 15, 20 and 13 — were read off the generated OpenAPI document with
`app.openapi()`, counting each path once per HTTP method:

| Prefix | Operations | Prefix | Operations |
| --- | --- | --- | --- |
| `/` and `/health` | 2 | `/knowledge` | 37 |
| `/api/v1/health` | 1 | `/analytics` | 18 |
| `/auth` | 11 | `/risks` | 6 |
| `/users` | 3 | `/recommendations` | 6 |
| `/projects` | 14 | `/intelligence` | 2 |
| `/tasks` | 16 | `/developer` | 14 |
| `/tags` | 5 | `/learning` | 19 |
| `/activity` | 2 | `/career` | 12 |
| `/planner` | 5 | `/ml` | 2 |
| `/calendar` | 5 | | |
| `/work-sessions` | 7 | | |
| `/availability` | 2 | | |

That is **142 paths and 189 operations**, of which 16 paths and 17 operations are the Phase 2
slice catalogued above. The 15-versus-20-versus-13 figures in the two phase reports were
counted before the remediation pass and are superseded by this table; each report carries the
correction in its own remediation section rather than being quietly edited.

### Route order is load-bearing

**Declare every literal sub-path above every parameterised one.** Starlette matches routes
in registration order and does not prefer a literal segment over a parameter. Were
`GET /developer/repositories/{repository_id}` registered above `/developer/summary`, the
literal string `summary` would bind to the path parameter, fail its uuid conversion, and
answer with a 422 about an id that never existed — while the dashboard tile quietly lost its
data.

There is no `Path` annotation that fixes this and no path-conversion trick; the order is
the entire mechanism. A test asserts it by hitting the literal route and checking for a
field only it returns.

The literal sub-paths today: `/summary`, `/metrics`, `/gaps`, `/activity`, `/features`,
`/goals`, `/skills`, `/profile`, `/experience`, `/evidence`, `/recommendations`,
`/repositories`, `/projects`, `/commits`.

### A page-size cap is a rejection, not a truncation

`?limit=500` is a **422**, not a silent 200 carrying 200 rows. A caller that asked for 500
and received 200 cannot tell a truncated page from a page that was always 200 rows long,
and a silent clip is the failure the [Pagination](#pagination) section exists to prevent.

The ceiling is `MAX_PAGE_SIZE = 200` and it is **the same number the repository clamps to**.
A smaller constant on the route would make a legal request look refused for a reason the
client cannot discover; a larger one would only be clipped silently further down.

`limit` and `offset` are `ge=1` / `ge=0`, and every filter narrows `total` as well as
`items` — which is why filtering lives on the server. A client-side filter can only see the
rows the current page happens to carry, so it can neither count a band nor offer a pager for
one.

### PATCH is a partial edit, applied with `exclude_unset=True`

Every `PATCH` in these phases applies its body as
`payload.model_dump(exclude_unset=True)`. `None` means "write SQL NULL" downstream, so
dumping the whole model would clear the description, the project link and the target skill
of any caller who only meant to rename something.

An omitted key leaves the column alone; an explicit `null` clears it. That is the only
reading under which a PATCH can both leave a description alone and blank it.

### An immutable stamp has exactly one producer

`LearningGoal.completed_at` is absent from `LearningGoalUpdate` and is written only by
`POST /learning/goals/{id}/complete`, which sets the status, stamps the timestamp and raises
progress to 100 in one call **because they are one fact**. The instant comes from the
**database** clock (`SELECT now()`), not from the request body, because the server's clock is
not evidence of when the user finished and a body could carry any instant at all.

`CareerEvidence.occurred_on` is the same idea from the other direction: it is required on
create, and an explicit `occurred_on: null` on patch is **refused** rather than stored. The
placeholder that would make undated evidence renderable would be a date the user never gave.

### An identity column is not editable

Fields that are part of a row's identity, or that only the system can measure, are **absent
from the `PATCH` payload**, and each absence is a rule:

| Field | Route that omits it | Why |
| --- | --- | --- |
| `local_path` | `PATCH /developer/repositories/{id}` | the row's identity, and the one field checked against the filesystem. Moving it would leave the counters, the commit range and the recorded history describing a directory this account never scanned |
| `primary_language` | same | measured by the scan, not typed by a person |
| `source`, `project_id`, `skill_id`, `repository_id` | `PATCH /career/evidence/{id}` | provenance is part of the row's uniqueness key. Re-pointing it would let a rename become a second record. The person is allowed to be wrong about *what* they wrote, and not about *where it came from* |
| `evidence_count`, `last_activity_at`, `confidence`, `level_source` | `PATCH /learning/skills/{id}` | the first two are NEXUS's own observations — a PATCH that could move them would let a skill claim six recorded sessions that do not exist, which the gap read would then quote as the evidence behind a level. The last two would let a client file its own inference as a self-assessment |
| `completed_at` | `PATCH /learning/goals/{id}` | see the one-producer rule above |

Unknown fields are **refused rather than dropped**, so "that field is not editable here" is a
422 naming the field rather than a cheerful 200 that discarded it.

### A write route returns the field the user must not set

Where a value is only ever derived by the system, the route sets it server-side and the
payload cannot override it. `POST /learning/skills` writes `level_source='user_defined'`
**unconditionally**, so a client cannot create a skill already carrying a `system_estimate`
whose evidence has not been recorded yet. Sending `current_level` on a `PATCH`
re-records the source as `user_defined`, because the person is the one making the claim now.

### Null, not zero — and a measured zero is a different answer

This is the rule these two phases add to the rest of the document, and it applies to every
figure that could not be computed:

| Situation | Wire | Screen |
| --- | --- | --- |
| A real measured zero | `0` | `0` |
| Could not be computed | `null`, with a reason where one exists | `—` |
| Insufficient data | `available: false`, `value: null`, a sentence saying why | *"Not enough data yet."* |

Applies to `/learning/features`, `/career/features`, `/developer/features` (the schema
versions `learning_features.v1`, `career_features.v1`, `developer_features.v1`),
`learning_minutes` on `/learning/summary`, `project_activity` on `/career/features` (null
until a repository has been scanned), and `repository_age_days` / `inactivity_days` on
`/developer/features`.

The rationale is the same in both directions: inside a training matrix a fabricated zero is
indistinguishable from an observed one once it reaches a trainer, and a `0` on screen reads
as a finding about the account rather than as an absence to explain.

### A read that can answer "I have none" is a 200

`GET /career/profile` answers **200 with a `null` body** for an account that has never written
one. Every other row in the API is addressed by an id the caller supplied, so a miss is a
404; the profile is addressed by nothing, and "you have not written one yet" is a state the
UI has to render rather than an absence it has to explain.

The service is never asked to fill the gap. A generated profile, a stub headline or a
placeholder summary would be the first career row NEXUS wrote.

Contrast: `/learning/summary`, `/career/summary` and `/developer/summary` answer 200 with
zeroes and `has_data: false` for an empty account. The flag is what tells a client to explain
an absence instead of rendering a dashboard of zeroes as a finding.

### A failed read is 200 with a status, not an exception

`POST /developer/repositories/{id}/scan` answers **200 whether the read worked or not**, with
`status: 'error'` and a human sentence in `error`. There is no background scheduler in NEXUS,
so this call *is* the scan; a repository that cannot be read is a completed attempt that
failed. No code path on that router produces a 500 for a bad directory.

### Dense series, and a service-owned window ceiling

- **Activity series are dense.** A quiet Tuesday arrives carrying `commits: 0` rather than
  being skipped, because a series that omits empty buckets compresses the timeline and makes
  a sparse fortnight read as dense as a busy one — a misreading of the data, not a
  presentational choice, and one a reader counting the bars cannot detect.
- **Only the lower bound of `window_days` is declared on the route.** The ceiling is a
  setting the service owns (`developer_max_window_days`, `learning_max_window_days`), and a
  constant in the router would answer 422 against a limit the deployment has raised.
- **`window_days` omitted is not the same request as a default one.** It asks the server for
  `*_default_window_days` rather than for a figure the router invented, and the response
  carries `window_days` back so any sentence a client writes about the figures can name the
  range.
- **Granularity omitted asks the server** for its configured default, and an unrecognised
  value is refused rather than guessed at.

### `analytics.read` guards the writes too

`analytics.read` is what the later module routers use for writes too. Developer, Learning
and Career take no capability of their own, and `Permission` gained no member in Phases 8
or 9 — `tests/test_permissions.py` asserts the complete set. Registering a repository,
writing a goal and entering a certification are all the caller answering a question about
rows derived from their own record, so the capability that admits the reading already
admits the answering. A new permission would be granted to exactly the roles
`analytics.read` already covers — the role table has no entry for either — while adding a
member the permission test asserts the complete set of.

This is a deliberate decision, not an oversight, and it is worth stating as one.

### Everything stays 404 over 403

No route in either phase takes a user id — not as a path segment, not as a query parameter,
not in a body. Every read and write resolves its row through an owner-scoped lookup, so
another account's row is **404, never 403**, identically to an id nobody ever issued. See
[Ownership answers 404](#ownership-answers-404-not-403).

---

## Conventions from Phase 11 — intent routing

Phase 11 added two operations under `/ml` and one error code. They are described here
rather than in the Phase 2 catalogue for the reason the Phases 3–9 routers are: the
catalogue was last revised for a slice this document inventories route by route, and the
interesting part of these two is what they refuse to do.

### The two routes

| Method | Path | Auth | Success | Notes |
| --- | --- | --- | --- | --- |
| `GET` | `/api/v1/ml/status` | bearer + `analytics.read` | 200, `MLStatusRead` — **including when ML is broken** | 401 unauthenticated, 403 without the permission, and nothing else. A disabled, unloaded or failed classifier is a 200 with `available: false` |
| `POST` | `/api/v1/ml/route` | bearer + `analytics.read` | 200, `RoutingDecisionRead` | 503 `ml_unavailable` when nothing can classify; 422 `validation_error` for text that is blank, over-long or credential-shaped; 500 if inference fails on a request it should have been able to answer |

Both take `AuthenticatedUser` rather than the plain current-user alias, because
`/ml/route` accepts arbitrary free text and answers "which of NEXUS's surfaces does this
mean" — an oracle over the taxonomy, free for an anonymous caller to enumerate, and
exactly the shape of a model-extraction probe. The session-aware alias is used because it
also honours revocation: a user who signed a device out must not keep routing through it
for as long as the access token is still valid.

Both are gated on `analytics.read`, which is a deliberate reuse rather than a missing
`ml.*`. Phases 7, 8 and 9 all gate new surfaces on existing capabilities, and
`tests/test_permissions.py` pins the `Permission` member set as a literal, so a new member
would be a test edit as well as a grant decision.

### `GET /api/v1/ml/status`

A degraded classifier is a **200**, and that is the health-endpoint precedent verbatim: the
endpoint reporting that a dependency is degraded is itself healthy. A 503 here would make
the one route that could explain an outage part of the outage, and every caller that asked
*what is wrong* would get *something is wrong* instead of the reason.

| Field | Notes |
| --- | --- |
| `enabled` | Whether ML is switched on for this deployment (`ML_ENABLED`) |
| `available` | Whether a working classifier can serve a prediction right now |
| `unavailable_reason` | `disabled`, `checkpoint_missing`, `runtime_missing`, `load_failed`, … — a closed vocabulary rather than prose, because it is the string an operator branches on. Null when available |
| `model` | The loaded checkpoint's identity, or `null` when nothing is loaded. An absent identity is a fact, not a hole in the response |
| `threshold` | The `ML_CONFIDENCE_THRESHOLD` this process routes with |
| `taxonomy_version` | `nexo_intents.v1` — the label set in use |
| `intents` | Every class the classifier predicts, with its description, destination, destination kind and service |

`intents` is the same table the router routes with, joined from the taxonomy rather than
written out, so a client that renders "surfaces NEXUS offers" from this response cannot
advertise a capability the router would refuse to route to.

### `POST /api/v1/ml/route`

The request body is one field, and nothing else is accepted:

```json
{ "text": "add a task to draft the migration plan for friday" }
```

| Rule | Why |
| --- | --- |
| `text` is required, `min_length=1`, `max_length=ML_MAX_INPUT_CHARS` (2000 by default) | The trained context is 128 subword tokens, so text past a couple of thousand characters is pure truncation: the bound exists to cap the request body, not to tune accuracy |
| The text is passed through byte for byte | Phase 10 trained on the raw dataset strings; trimming or lower-casing here would be a distribution shift the model has never seen |
| **Unknown keys are refused, not dropped** | Pydantic's default is to ignore an extra field, which would answer `{"text": …, "intent": …}` with a cheerful 200 and no sign that the client had tried to steer the classifier |
| The caller's text is never logged | Only the intent, the confidence, the latency, the truncation flag and the character count are recorded |

A second, higher ceiling of 4 000 characters lives in the classifier itself rather than in
the schema, so that every caller — the route, a batch path, a script — is held to the same
rule. The route's own bound is lower, so on HTTP the schema answers first.

The response:

| Field | Notes |
| --- | --- |
| `intent` | The winning intent name; always one of the fourteen |
| `confidence` | **This utterance's** softmax probability. Not the model's test-set accuracy, and not a calibrated probability of being right |
| `threshold` | The confidence below which NEXUS declines to name a service |
| `status` | **The field to branch on.** One of `accepted`, `uncertain`, `out_of_scope`, `generation_unavailable` |
| `destination` | An `api/v1/...` prefix, `large-model:unavailable`, or `abstain` |
| `destination_kind` | `router`, `large_model`, or `fallback` |
| `target` | `{service, module, entrypoint}` — a **pointer**, or `null` |
| `reason` | Human-readable and safe to render. On `uncertain` it names the runner-up intents, so a client can offer "did you mean…?" |
| `alternatives` | Up to three runner-up intents with their probabilities, highest first |

All four statuses are **200**. `uncertain`, `out_of_scope` and `generation_unavailable`
are answers the classifier gave on purpose, and each carries a `reason` the caller can
show. Only a runtime that cannot classify at all is an error, and it fails closed:

> `target` is `null` for `uncertain`, `out_of_scope` and `generation_unavailable` — for
> **all fourteen intents**. That is the safety property: a low-confidence request can never
> reach a service, least of all a mutating one.

### The failures, precisely

| Condition | Status | `code` | `details` |
| --- | --- | --- | --- |
| No working classifier — `ML_ENABLED=false`, no checkpoint on disk, no `torch`, or a load that failed | **503** | `ml_unavailable` | `reason`: the runtime's own vocabulary. Never a filesystem path |
| `text` empty or whitespace-only | 422 | `validation_error` | `reason: "blank"`, `max_characters` |
| `text` longer than the configured bound | 422 | `validation_error` | `max_characters`, `characters` |
| `text` contains credential-shaped content (a live API key, a private-key block, a bearer token) and `ML_REJECT_CREDENTIALS` is on | 422 | `validation_error` | `reason: "credential_shaped"` and the **kind**. Never the matched value |
| The model loaded and then failed on this request | 500 | `internal_error` | `null` — the fixed message, per [5xx is deliberately opaque](#5xx-is-deliberately-opaque) |

The 503 is the important one, and it is a refusal rather than a failure: NEXUS will not
answer with an invented intent. A caller's next move is to call a service on the strength
of the answer, so a fabricated prediction would be NEXUS inventing a user's instruction and
then acting on it.

### What this surface will not do

Four refusals, each of which is a rule rather than an omission:

- **No logits, no tensors, no tokenizer ids, no stack frames, no absolute paths.** The
  classifier's internals stop at a plain dataclass before anything leaves the process. A
  client that could see them would be coupled to the checkpoint, and a checkpoint can be
  retrained without a client changing. The resolved checkpoint path appears in exactly one
  place — `model.checkpoint` on the diagnostics route — for an authenticated operator.
- **The caller cannot choose which checkpoint answers.** `ML_MODEL_PATH` is deployment
  configuration, reported on `/ml/status` and never accepted on `/ml/route`; a caller
  able to set it would be choosing which weights answer them.
- **The decision names a service; it does not call one.** Slot-filling an utterance into
  `TaskService.create(...)` would need a second model or hand-written per-utterance
  parsers. The caller makes the call, through the same authenticated, owner-scoped route
  they would have used had they typed the request themselves.
- **Two intents reach no service at all.** `code_assist` and `deep_reasoning` are trained
  classes the model *will* return, and their destination is exactly
  `large-model:unavailable`. NEXUS runs no generative model and says so rather than
  answering a code question with a confident non-answer.

### What `confidence` is not

`confidence` is a softmax probability for this utterance. The threshold shipped with it,
**0.90**, is an *integration* threshold: it was chosen by re-running the checkpoint over
the 420-row held-out training split and measuring the trade between coverage and precision
(0.90 keeps 95.2% of utterances and lifts precision on accepted requests from 0.9738 to
0.9900). That is a number about the synthetic, template-generated corpus of Phase 10.

On natural-language phrasings written specifically to break the classifier — lower case,
ALL CAPS, no question mark, terse mobile phrasing, vocabulary away from the domain nouns
the synthetic corpus leans on — it was right **42 of 56 times, 75.0%**. Seven of those
fourteen misses were predicted at 0.82 or above and four at 0.90 or above, so the
threshold does not catch them: **a model that is confidently wrong is confidently wrong.**
A client should therefore branch on `status` and present `reason` and `alternatives` to the
user, not treat `accepted` as a command. The full measurement and the fourteen misses are
in [`specifications/phase-11-report.md`](specifications/phase-11-report.md).

---

## Health semantics

Two endpoints, two jobs. Do not merge them and do not move readiness to
`/health`.

| | `GET /health` | `GET /api/v1/health` |
| --- | --- | --- |
| Purpose | Liveness | Readiness + metadata |
| Touches the database | Never | `SELECT 1`, timed |
| Body | `{"status":"ok"}` | Full report, below |
| Status when the DB is down | 200 | 200 with `status: "degraded"` |

```json
{
  "status": "healthy",
  "app": "NEXUS",
  "version": "0.1.0",
  "environment": "development",
  "database": { "status": "connected", "latency_ms": 1.83 },
  "uptime_seconds": 128.44,
  "timestamp": "2026-01-01T00:00:00.000Z"
}
```

`status` is `healthy` when the probe succeeds and `degraded` when it fails;
`database.status` is `connected` or `unavailable`; `uptime_seconds` comes from
`time.monotonic()` since process start.

**A degraded database is still a 200.** The endpoint reporting that the service is
degraded is itself healthy, and the frontend's health card reads
`status`/`database.status` from a 200 body. Returning 503 here would make every
client treat a database outage as a server outage, and would let a dependency
failure drive a restart loop.

---

## The client contract

Everything a caller needs lives in three frontend places. Keep them in lockstep
with the backend schemas; a mismatch is a runtime failure, not a compile error.

| File | Role |
| --- | --- |
| `frontend/src/lib/api-client.ts` | `ApiClient`: base URL, bearer injection, query building, 30 s timeout, `ApiError` |
| `frontend/src/services/*.ts` | One function per endpoint (`auth.ts`, `sessions.ts`, `users.ts`, `health.ts`) — no fetch logic elsewhere |
| `frontend/src/types/api.ts` | Wire types mirroring the Pydantic models, including `UserRead`, `SessionRead` and `TokenPair.session_id` |

Client behaviour worth relying on:

- **Every failure is an `ApiError`** with `status`, `code`, `message`, `details`,
  `requestId` and convenience getters (`isUnauthorized`, `isValidationError`, …).
  Normalise anything else through `toApiError` from `services/errors.ts` before
  rendering it.
- **The token is injected, not passed.** The client calls a `TokenGetter` supplied
  by the auth store; pass `{ auth: false }` for public endpoints so no bearer
  header is sent to `login`, `register`, `refresh` or `health`.
- **The body `request_id` wins** over the header when both are present.
- **204 and `parse: 'none'` return `undefined`**, not `{}`.
- **Query values that are `null` or `undefined` are dropped**, not serialised as
  the strings `"null"` / `"undefined"`.
- **Absolute paths bypass the base URL.** `apiClient.get('http://…')` is passed
  through unchanged; every other path is resolved against the base.

`ApiErrorCode` is a closed union of today's codes widened with `(string & {})`, so
a new backend code type-checks without a frontend change but will not autocomplete.

---

## Checklist for a new endpoint

Router (`app/api/v1/<module>.py`) — thin, no business rules, no SQL:

- [ ] Declared on a router with `prefix` and `tags`; registered in
      `app/api/v1/router.py`.
- [ ] `response_model` is a dedicated `…Read` schema, not the ORM model.
- [ ] Explicit `status_code` (201 for creation) and a one-line `summary`.
- [ ] Identity comes from a dependency: `AuthenticatedUser` for endpoints that
      must honour revocation, `CurrentUser` for the rest, `SuperUser` for
      privileged ones. Never read the token in the handler.
- [ ] A capability-gated route carries `Depends(require_permission(Permission.…))`
      in `dependencies=`, naming the capability rather than the role. Do not
      hard-code a role comparison in the handler.
- [ ] A route acting on a caller-scoped row scopes its lookup by the caller's id
      and answers **404**, not 403, when the row is not theirs.
- [ ] Write access goes through `Depends(get_*_service)`, which builds the
      request-scoped session → repository → service chain.
- [ ] No `HTTPException` and no hand-built error body. Raise `NotFoundError`,
      `ConflictError`, `ForbiddenError`, `ValidationError` from the service.

Service (`app/services/<module>_service.py`) — rules, no FastAPI imports:

- [ ] Raises a domain error for every failure mode; never returns `None` to mean
      "not found" from a public method (`UserService.get_by_id` is the pattern).
- [ ] Email/identifier normalisation and uniqueness are enforced here, not
      inferred from the ORM.
- [ ] A raw `IntegrityError` is translated to `ConflictError` so a race cannot
      leak a driver error.

Repository (`app/repositories/<module>.py`) — SQL only, no domain errors:

- [ ] Returns `None` for a miss and lets unexpected driver errors propagate.
- [ ] Uses the `User`-style mixins: application-side UUID primary key,
      `TimestampMixin` for `created_at` / `updated_at`.

Schemas (`app/schemas/<module>.py`):

- [ ] `…Read` uses `ConfigDict(from_attributes=True)`; no secret field appears.
- [ ] Constraints live here, so an invalid payload is a 422 with field details.
- [ ] Nullable fields default to `None` and are always serialised.
- [ ] List endpoints return a list envelope, not a bare array — and the one they
      return is the one that router's neighbours return. Phases 3–5 return
      `Page[ItemRead]` (counters nested under `meta`); Phases 7–9 return a flat typed
      list response. See [Pagination](#pagination).

Cross-cutting:

- [ ] The endpoint is reachable under `/api/v1/…` and appears in Swagger with a
      useful summary.
- [ ] Anything security-relevant the new service does is written to the audit
      trail (`AuditService.record`) with **no password, token or hash of either**
      in `metadata` — that column is retained longer than the sessions it
      describes.
- [ ] `frontend/src/services/` gains exactly one function, and
      `frontend/src/types/api.ts` gains the matching wire type.
- [ ] A test asserts the success shape *and* `assert_error_envelope` +
      `assert_no_internals` for every failure path. A path that can raise
      unhandled is exercised with `non_raising_client`, so the rendered 500 is
      asserted rather than re-raised.
- [ ] If it can return a 422, the request body fields are named in the schema so
      `details.errors[].field` addresses them the way a form does — the login
      and register pages already render that field path, and a new form will
      copy them.
- [ ] No 5xx path depends on its `detail` reaching the client; see
      [5xx is deliberately opaque](#5xx-is-deliberately-opaque).
- [ ] A migration exists for every schema change — `alembic revision
      --autogenerate -m "…"`, then confirm with `alembic check`.

---

## Worked example — the shape a future endpoint takes

**Written before Projects shipped, and kept as the pattern rather than as a claim.** The
code below is a worked slice — model, schema, repository, service, provider, router —
written against the real imports and the real conventions. Projects exists today, so read
this as the shape of the *next* module: the three still without a backend are Search, AI
Assistant and Experiments. A route that gates on a capability adds one line to the
decorator — `dependencies=[Depends(require_permission(Permission.PROJECTS_WRITE))]` — and
nothing else in the slice changes.

Router:

```python
"""Project endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import get_project_service
from app.models.project import Project
from app.schemas.project import ProjectCreate, ProjectRead
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    payload: ProjectCreate,
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> Project:
    return await service.create(payload)
```

The imports are the point of the example, not decoration: `Project` (the ORM
model) is what the handler returns, `ProjectRead` is what leaves the process,
`ProjectService` names both the service and the annotation, and
`get_project_service` is the one dependency that builds the
session → repository → service chain. A router that annotates a service it did
not import, or imports an identity dependency it never uses, is the shape this
document exists to prevent.

Service — the only place a domain error is raised:

```python
async def create(self, data: ProjectCreate) -> Project:
    if await self.repository.exists_by_name(data.name):
        raise ConflictError("A project with this name already exists.")
    return await self.repository.create(name=data.name, description=data.description)
```

Response shapes to match:

```json
{
  "id": "6f1c…",
  "name": "NEXUS",
  "description": null,
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

Note what the client receives on the duplicate: `409`,
`{"error":{"code":"conflict","message":"A project with this name already exists.","details":null,"request_id":"…"}}`
— no uniqueness detail that the caller could not have derived, and no traceback.

---

## Testing an endpoint

Backend tests live in `backend/tests/`. Run them from `backend/`:

```bash
# the whole suite — 2270 collected (needs the nexus_test database, created automatically)
python -m pytest

# the 1029 database-free tests — 1241 deselected
python -m pytest -m "not integration"

# one file
python -m pytest tests/test_errors.py -v
```

Use `backend/.venv/bin/python` (or `backend\.venv\Scripts\python.exe`) unless the
interpreter on your PATH already has the dependencies.

Two helpers and one client fixture in `backend/tests/conftest.py` and
`tests/test_errors.py` carry most of the contract. The first example is
illustrative — it uses the Phase 2 `projects` resource:

```python
async def test_duplicate_project_name(client, assert_error_envelope):
    await client.post("/api/v1/projects", json={"name": "NEXUS"})

    response = await client.post("/api/v1/projects", json={"name": "NEXUS"})

    error = assert_error_envelope(response, status_code=409, code="conflict")
    assert error["request_id"] == response.headers["X-Request-ID"]
```

- `assert_error_envelope(response, status_code=…, code=…)` asserts the exact key
  set, the code, a non-empty message, and that the body's `request_id` matches the
  response header.
- `assert_no_internals(response)` in `tests/test_errors.py` fails the test if the
  body leaked a traceback, driver name, SQL keyword or internal package path.
  Use it on every failure path, not just one.
- `non_raising_client` in `backend/tests/conftest.py` builds the app's
  `AsyncClient` with `ASGITransport(raise_app_exceptions=False)`, so an
  exception that escapes a handler is observed as the **rendered 500** instead of
  being re-raised inside the test. It is the only way to assert on the 5xx
  envelope itself — the default `httpx` behaviour re-raises and the response the
  user would have received is never visible. Use it whenever a test deliberately
  provokes a failure; use `offline_client` or `client` everywhere else. Like the
  others it depends on `app` alone, so a database-backed test can add
  `truncated_database` to its signature:

  ```python
  async def test_unhandled_failure_is_opaque(non_raising_client, assert_error_envelope, assert_no_internals):
      response = await non_raising_client.get("/api/v1/…")  # a route that raises

      error = assert_error_envelope(response, status_code=500, code="internal_error")
      assert error["message"] == "An internal server error occurred."
      assert error["details"] is None
      assert_no_internals(response)
  ```

  That last pair of assertions is the machine-readable form of
  [5xx is deliberately opaque](#5xx-is-deliberately-opaque); any new 5xx path
  should carry them.

Conventions that the suite encodes:

- A test that touches the database is marked `integration`; anything that can run
  without one should use `offline_client` and stay unmarked. The
  `pytest -m "not integration"` subset must pass with PostgreSQL stopped.
- The schema under test comes from Alembic, never from `Base.metadata.create_all`
  — a schema built from the models would prove nothing about the migration.
- Frontend: `npm test` from `frontend/` (645 tests, 44 files) runs Vitest with React
  Testing Library; `npm run typecheck` and `npm run lint` must also be clean.

**What is verified and what is not.** The frontend figures above are a real pass count —
`npm test` was run end to end during the final remediation pass and reports **44 files,
645 tests, all passing**. The backend figures are **collection** counts, taken from
`pytest --collect-only`, and are labelled that way on purpose: collection proves what the
suite contains, not that it passes. They were captured before Phases 10 and 11 added
anything, so they understate the suite today; the most recent full backend run is recorded
in [`development.md`](development.md) §10.

```bash
# Phase 11 — needs the trained checkpoint and torch; skips cleanly without them
python -m pytest tests/test_ml_integration_*.py
```

What is **not** verified here is anything that needs a container. `docker compose up` has
never been run: Docker is not installed in this environment, so `docker-compose.yml`
remains statically validated by `scripts/verify_compose.py` and nothing more. The chain
itself *is* exercised — `alembic upgrade head` is applied by the `test_database_url` fixture
on every integration session and `test_migrations.py::test_autogenerate_reports_no_drift`
compares the result with `Base.metadata`, which is the same comparison the `alembic check`
command performs.

---

## Remediation pass over Phases 1–9

The last thing that happened to this codebase before ML training was an audit, and it found
real defects rather than cosmetic ones. Four engineers fixed them on disjoint files and the
documentation was corrected to match. This section records what changed on the wire and in
this document, so that a reader who trusts the rest of the file is not misled by an older
paragraph they remember.

### Wire changes a client can observe

| Change | What it was | What it is now |
| --- | --- | --- |
| `GET /api/v1/analytics/projects` returns `Page[ProjectAnalyticsRead]` | A bare `ProjectAnalyticsRead[]` | The `meta`-nested envelope, joining the other fourteen `Page[T]` operations. A client that iterated the response directly must read `.items` |
| `GET /api/v1/analytics/feature-snapshot` wraps its result | A bare mapping of feature columns, with no version key | `{schema_version: "analytics_features.v1", generated_at, task_id, features: {…}}` — the version sits **beside** the matrix, never inside it |
| `GET /api/v1/developer/features` omits a never-scanned repository | A row of zeros for every registered repository | No row at all for a repository that has never been successfully scanned. A row that exists means the scan ran and found nothing, which is a measurement |
| `PUT /api/v1/availability` is atomic | Delete-then-insert, committed separately: a payload the table refused left the user with **zero** rules | One transaction. A refused replace leaves the previous week completely intact |
| A deadline sentence names the task's own `due_date` | `detected_at + deadline_in_hours`, two clocks summed, which lands a whole day late on any pass that begins before midnight and writes after it | The `due_date` column, read |
| `429` is reachable | `rate_limited` was a reserved code with no implementation | `RateLimitMiddleware` answers 429 with the shared envelope. See [Rate limiting](#rate-limiting) |

### Documentation corrections in this file

- **`Page[T]` was documented as unserved.** It is served by fifteen operations, and the
  frontend's `Paginated<T>` — which the file also called a mismatch — has carried the correct
  `meta` nesting for some time and is consumed by every knowledge, planner and work service.
  Both errors are gone.
- **Operation counts were stale.** The file said 136 paths and 183 operations; the live
  schema had **140 paths and 187 operations** when this pass landed, and Phase 11 has since
  taken it to 142 and 189. The per-prefix table under
  [Conventions from Phase 8 and Phase 9](#conventions-from-phase-8-and-phase-9) was rewritten
  from `app.openapi()`.
- **`rate_limited` was listed as a known gap.** It is implemented.
- **The revocation-denylist bullet appeared twice**, identically, in the same list.
- **Test counts were stale**: 2090/1011/1079 for the backend, 607 in 42 files for the
  frontend. See [Testing an endpoint](#testing-an-endpoint).

### Invariants this document now holds itself to

`backend/tests/test_documentation_claims.py` reads these files and fails when a claim
regresses: that every `Settings` field is documented in both `.env.example` and the README,
that the stated `Page[T]` count matches a live count of `app.openapi()`, that the frontend
`Paginated<T>` really does nest under `meta`, that `maintenance_activity` is not described
as reading low when the shipped path reads at its ceiling, that the superseded figures above
are absent, and that the four productivity weights are documented as a start-up gate. A
number in prose with nothing checking it is a number that drifts; these are the ones that had
drifted.

---

## Known gaps

Most of these are Phase 2 omissions, and none of them was closed by Phases 3–11. Two that
used to sit in this list — the claim that `rate_limited` had no producer, and
the claim that the frontend's `Paginated<T>` disagreed with the backend's `Page[T]` — were
**false**, not open, and the [Remediation pass](#remediation-pass-over-phases-19) removed
them. Read what is left
alongside [Conventions from Phase 8 and Phase 9](#conventions-from-phase-8-and-phase-9),
which records what the later phases added:

- **Error responses are absent from the OpenAPI schema.** Operations declare only
  their success codes, so Swagger UI does not render the envelope. Fix by adding
  a shared `responses={...}` model and referencing it from each route.
- **The ML endpoints are unusable on a fresh clone, by design.**
  `backend/ml/artifacts/` is gitignored, so a checkout that has never run the Phase 10
  training pipeline has no checkpoint and `POST /api/v1/ml/route` answers **503
  `ml_unavailable`** with `reason: "checkpoint_missing"`. `GET /api/v1/ml/status` is the
  route that explains it. This is a supported state, not a broken install, but it does mean
  the ML surface is the one part of the API a new contributor cannot exercise without first
  training a 703 MiB checkpoint.
- **`GET /api/v1/users/` is a fixture, not a feature.** It exists so the role →
  permission wiring has a route whose refusal is observable end to end. It
  returns an unbounded list with no pagination, because a fixture that could
  itself need pagination would be a worse fixture. Do not build UI on it.
- **`audit_log_retention_days` is declared but not enforced.** No job prunes
  `audit_logs`; the setting states a policy and gives the value somewhere to be
  displayed. There is no endpoint to read the audit trail either.
- **The access-token revocation denylist is in-process** — not restart-durable,
  not shared between workers. Refresh tokens and sessions are database-backed and
  do not have this problem; see [Revocation store](#revocation-store-and-its-limits).
- **The client's password checklist is very slightly stricter than the server**
  for exotic numeric characters (`\p{Nd}` vs Python's `str.isdigit()`). Documented
  in [Password policy](#password-policy); the divergence is one-way and harmless,
  because the server is the only authority.
- **The rate limiter's counters are in-process** — a fixed window held in memory,
  so a restart resets every budget and N workers each enforce their own. It is a
  backstop against a runaway or parallelised client, not a distributed quota. See
  [Rate limiting](#rate-limiting).
- **`Message` is declared but unserved.** No endpoint returns it. (`Page[T]` *is*
  served — by fifteen operations. The Phases 7–9 routers return their own flat
  list envelopes instead; see the note under [Pagination](#pagination).)
- **No idempotency keys, no ETags, no cursor pagination, no bulk endpoints.** The
  first two phases do not need them; add them as real use cases appear rather than
  as speculative machinery.
- **`GET /api/v1/health` is unauthenticated**, which is correct for a readiness
  probe on a local-first, single-user system bound to loopback. It exposes
  `environment`, `version` and uptime and nothing else.
- **There is no audit-trail endpoint.** Rows are written and never read by the
  API. Nothing in the product surfaces them yet.

> `bad_request` is never raised deliberately, but it is *reachable*:
> `_status_code_to_code` returns it for any unmapped 4xx, so it is the code a 413 or
> 415 will carry.
