# Phase 1 QA — Ruthless first-time user (probe `p01a`)

**Scope covered:** `backend/app/main.py` (factory, lifespan, `/`, `/health`),
`backend/app/core/{config,logging,middleware,exceptions,event_loop}.py`,
`backend/app/db/{base,session}.py`, `backend/app/api/v1/health.py`,
`backend/app/schemas/health.py`, `backend/run.py`, `backend/alembic.ini`,
`backend/migrations/{env.py,README.md,versions/*}`,
`frontend/src/services/health.ts`, `frontend/src/features/health/*`,
`frontend/src/lib/api-client.ts`, `frontend/src/services/errors.ts`,
`frontend/src/components/feedback/error-state.tsx`,
`frontend/src/app/query-client.ts`, `frontend/vite.config.ts`,
`README.md`, `docs/api-conventions.md`, `docs/development.md`, `.env.example`,
`scripts/{bootstrap.py,wait_for_db.py}`, the real OpenAPI dump, and
`tests/test_health.py`, `tests/test_observability.py`, `tests/test_documentation_claims.py`.

Persona applied: I did not read the docs first. I cloned the idea "type
`python run.py`, open the URL the README names, see it work", and recorded every
point where I had to guess, wait, or read source to find out what was happening.

**Runs executed** (all real, all output pasted below or quoted inline):

| # | Command | Result |
| --- | --- | --- |
| 1 | `backend/.venv/Scripts/python.exe -c "from app.main import app; ..."` | `paths: 145` / `operations: 192` |
| 2 | ASGI in-process client over `/health`, `/api/v1/health`, `/`, `/openapi.json` | `/health` 200, `/api/v1/health` **500**, `/` 200 |
| 3 | `pytest tests/test_observability.py -q -k probe_answers_a_verdict` | **1 failed**, `AsyncContextNotStarted` |
| 4 | `python -m alembic heads` / `history` / `upgrade head --sql` | `0011 (head)`; 11 revisions; 886 lines of offline SQL, exit 0 |
| 5 | `NEXUS_PORT=8931 ML_ENABLED=false python run.py` + `curl` ×3 | real uvicorn boot; `/health` 200, `/api/v1/health` **500** |
| 6 | Direct `check_database_connection()` under `nexus_loop_factory` | `RAISED AsyncContextNotStarted` at 3094/3010/3005 ms |
| 7 | Error-envelope sweep: 404 / 405 / 422 / malformed JSON / 401 | all enveloped; see finding P01A-07, P01A-08 |
| 8 | Rate limiter with `Settings(rate_limit_general_max_requests=3)` | 200×3 then 429 + `Retry-After: 60`; `X-Forwarded-For` correctly ignored |
| 9 | `LOG_REQUEST_BODY=true` + CORS sweep | `password` and `access_token` redacted; CORS correct |
| 10 | `python -m alembic upgrade head` (no DB) | **no output for 120 s, killed, exit 124** |
| 11 | `pytest tests/test_health.py tests/test_documentation_claims.py -m "not integration"` | `14 passed, 3 deselected` |
| 12 | `scripts/bootstrap.py --skip-install` | exit 0, correct next steps |
| 13 | `scripts/wait_for_db.py --timeout 6` | exit 1 with a named remedy and a redacted DSN |
| 14 | vitest probe `frontend/src/__probe.p01a.probe.test.tsx` (created, run, **deleted**) | `2 passed`; printed the exact strings a user sees |
| 15 | OpenAPI response-code census over all 192 operations | `{('200','422'): 134, ('201','422'): 20, ('204','422'): 23, ('200',): 12, ('202','422'): 2, ('204',): 1}` — **no 401/403/404/429/500 anywhere** |

---

## Findings

### P01A-01 — S1 — `GET /api/v1/health` returns 500 `internal_error` instead of 200 `degraded` whenever PostgreSQL is unreachable

- **Component:** `backend/app/db/session.py:95-106` (the `finally:` at 103-106); consumer `backend/app/api/v1/health.py:47`; mask `backend/app/main.py:127-130`
- **Category:** correctness / contract / error-handling
- **Evidence:** executed, against a real uvicorn server (`python run.py`, port 8931, no PostgreSQL on this host):

  ```
  $ curl -s -i http://127.0.0.1:8931/health
  HTTP/1.1 200 OK
  x-request-id: f0530c90-c591-422c-94f5-3b99d2654fd7
  {"status":"ok"}

  $ curl -s -i http://127.0.0.1:8931/api/v1/health
  HTTP/1.1 500 Internal Server Error
  x-request-id: dd258245-da6f-440d-8f09-56edbe79869b
  {"error":{"code":"internal_error","message":"An internal server error occurred.","details":null,"request_id":"dd258245-da6f-440d-8f09-56edbe79869b"}}
  ```

  Root cause, confirmed by driving the function alone:

  ```
  $ python - <<'EOF'   # asyncio.run(..., loop_factory=nexus_loop_factory)
  attempt 0: 3094.9 ms -> RAISED AsyncContextNotStarted
  attempt 1: 3010.7 ms -> RAISED AsyncContextNotStarted
  attempt 2: 3005.1 ms -> RAISED AsyncContextNotStarted
  EOF
  ```

  `get_engine().connect()` returns an un-awaited `AsyncConnection`. When the
  handshake fails, `async with connection:` never completes, so `__aexit__`
  never runs — and the `finally:` at `session.py:106` calls `await
  connection.close()` on that un-started object, which raises
  `AsyncContextNotStarted`. Because it raises from `finally`, it *replaces* the
  `return False` the `except Exception:` branch just produced. The function's
  documented contract ("Return `True` when a trivial query succeeds", "Overrunning
  is reported as unavailable") is therefore unreachable: it raises on every
  failure path.

  An existing test in the repository already asserts the contract and fails here:

  ```
  $ pytest tests/test_observability.py -q -k probe_answers_a_verdict
  ...
  app\db\session.py:106: in check_database_connection
      await connection.close()
  ...
  E  sqlalchemy.ext.asyncio.exc.AsyncContextNotStarted: AsyncConnection context
     has not been started and object has not been awaited.
  FAILED tests/test_observability.py::test_the_database_probe_answers_a_verdict_and_never_raises
  1 failed, 27 deselected
  ```

  The contract this breaks is stated in three places I was expected to trust:
  - `docs/api-conventions.md:1234` — "| Status when the DB is down | 200 | 200 with `status: "degraded"` |"
  - `docs/api-conventions.md:1252` — "**A degraded database is still a 200.**"
  - `backend/app/api/v1/health.py:4-6` — "A degraded database still yields `200`"
  - `README.md:1082-1085` — "**Symptom.** `/health` is green, but `/api/v1/health` reports `"status": "degraded"`"
  - `frontend/src/types/api.ts:215` — "still 200 when the database is down, but `degraded`"

  The test that claims to cover it, `backend/tests/test_health.py:68`
  (`test_a_database_fault_degrades_readiness_without_failing_it`), monkeypatches
  `check_database_connection` with a function that **returns `False`** — so it
  stubs away the exact code path that breaks, and passes. I ran it: it passes.

- **Steps to reproduce:**
  1. Have PostgreSQL stopped or unreachable (this host has nothing answering on 5432).
  2. `cd backend && python run.py`
  3. `curl -i http://127.0.0.1:8000/api/v1/health`
  4. Observe `500` and `{"error":{"code":"internal_error",...}}`.
  5. Compare `/health`, which correctly returns `200 {"status":"ok"}`.

- **Expected:** `200` with `{"status":"degraded","database":{"status":"unavailable","latency_ms":...}, ...}`.
- **Actual:** `500` with the opaque `internal_error` envelope; the request also takes the full `DB_PROBE_TIMEOUT_SECONDS` (measured **3.0 s**) before failing.
- **Impact:** The endpoint whose entire job is to report a degraded database cannot report one — it crashes instead. Every first-run path a newcomer takes hits this: the README's own troubleshooting section tells them to look at `/api/v1/health` and shows a `degraded` body they will never see; `scripts/wait_for_db.py` says `no PostgreSQL server answered on that host and port` while the API says `An internal server error occurred`. Secondary damage: `main.py:127-130` wraps the same call in a blanket `except Exception: database_ready = False`, so the boot log prints `database_probe database=unavailable` — the right verdict for the wrong reason. Any future bug inside the probe would be reported identically, as "the database is unavailable".
  Blast radius is bounded: `docker-compose.yml:115` healthchecks `/health` (liveness), not `/api/v1/health`, so the Compose stack itself does not break. What breaks is the documented readiness endpoint, the frontend health card, and every human or script that reads it.
- **Suggested fix:** make the probe able to return `False`. Either drop the `finally` and let `async with connection:` manage its own cleanup (SQLAlchemy closes a connection whose `__aenter__` failed), or guard the close:
  ```python
  finally:
      with contextlib.suppress(Exception):
          await connection.close()
  ```
  Prefer the first: the `finally` exists to handle the timeout-abort case, which `async with connection:` already handles for the connect-failure case; keeping it and suppressing is the smaller diff. Either way, add a test that drives the *real* probe with no database (or with `get_engine` returning a stub whose `connect()` raises) and asserts `is False`, rather than patching the probe to return `False`. `test_observability.py:433` already exists for exactly this and currently fails.

---

### P01A-02 — S2 — The OpenAPI spec declares the *wrong* body shape for every 422 and declares no error status at all, on all 192 operations

- **Component:** `backend/app/core/exceptions.py:260-275` (real 422 body) vs. the FastAPI-generated `components.schemas.HTTPValidationError`; no `responses=` override anywhere in `backend/app/api/v1/`
- **Category:** contract / docs
- **Evidence:** real OpenAPI dump:

  ```
  POST /api/v1/auth/login responses:
    "200": {"content":{"application/json":{"schema":{"$ref":".../TokenPair"}}}}
    "422": {"content":{"application/json":{"schema":{"$ref":".../HTTPValidationError"}}}}

  HTTPValidationError schema:
    {"properties":{"detail":{"items":{"$ref":".../ValidationError"},"type":"array","title":"Detail"}},
     "type":"object","title":"HTTPValidationError"}
  ```

  The body the server actually returns for that same request (executed):

  ```
  422 {"error":{"code":"validation_error","message":"The request body or query parameters failed validation.",
       "details":{"errors":[{"field":"email","message":"String should match pattern '...'",...}]},
       "request_id":"b729474e-bfed-419e-9b2c-d43c89e95217"}}
  ```

  Census of declared response codes across all operations:

  ```
  {('200','422'): 134, ('201','422'): 20, ('204','422'): 23, ('200',): 12, ('202','422'): 2, ('204',): 1}
  operations declaring 429: 0
  ```

  So: `{detail: [...]}` is documented and `{"error": {...}}` is sent; and 401, 403, 404, 409, 429 and 500 are documented nowhere in the schema — despite `docs/api-conventions.md` devoting a section to each and `middleware.py` being able to refuse *any* route with a 429.

- **Steps to reproduce:**
  1. `cd backend && python -m uvicorn app.main:app` (or read the dump with `app.openapi()`).
  2. Open `http://localhost:8000/docs`, expand `POST /api/v1/auth/login` → Responses.
  3. Only 200 and 422 appear. Try the endpoint with a bad body: you get `{"error":{...}}`.
- **Expected:** the spec describes the responses the server produces, including the shared error envelope and the codes a caller can actually receive.
- **Actual:** the 422 schema is FastAPI's default and is simply wrong; no other non-2xx code is described anywhere.
- **Impact:** Swagger is named in `README.md:737` as the onboarding path ("Create an account from the **Authorize** button in Swagger"), so this is the first contract a first-time user reads, and it misdescribes the one error they are most likely to hit. Any generated client (openapi-generator, orval, a typed fetch wrapper) will type a 422 as `{detail: [...]}` and mis-handle every validation failure.
- **Suggested fix:** add a `responses=` mapping to `create_app`'s routers via a shared dict (an `ErrorEnvelope` pydantic model in `app/schemas/` plus `{401,403,404,409,422,429,500}` refs), or set `app.openapi_schema`-level default responses. Cheapest correct version: define the error model once and add a router-level `responses` default in `app/api/v1/router.py`. Do not delete the FastAPI-generated 422 without replacing it — the real 422 body is the envelope.

---

### P01A-03 — S2 — `alembic upgrade head` hangs forever with zero output when PostgreSQL is unreachable

- **Component:** `backend/migrations/env.py:112`
- **Category:** error-handling / docs
- **Evidence:**

  ```
  $ cd backend && timeout 120 python -m alembic upgrade head
  (no output at all, for 120 s)
  EXIT=124
  ```

  Nothing is printed because `fileConfig` sets the root logger to `WARNING`
  (`alembic.ini:54`), Alembic's "Context impl PostgresqlImpl." line is emitted
  from inside `context.configure()` — which only runs *after* a connection is
  established — and the connect itself never returns.

  `env.py:112` builds the engine with no bound of any kind:
  ```python
  connectable = create_async_engine(get_url(), poolclass=pool.NullPool)
  ```
  Compare the two other paths in this same repository, which both bound the same
  operation: `app/db/session.py:97` wraps the connect in
  `asyncio.timeout(settings.db_probe_timeout_seconds)`, and
  `scripts/wait_for_db.py:51` passes `connect_timeout=CONNECT_TIMEOUT` to psycopg
  explicitly. Verified: `wait_for_db.py` returns in 6 s with a full diagnosis;
  alembic does not return at all.

  This is documented step 3 of the local quick start (`README.md:798`:
  `cd backend && ../backend/.venv/bin/python -m alembic upgrade head`) and of
  `make migrate` (`README.md:937`), and it is the backend container's start
  command (`README.md:540`: `alembic upgrade head && exec python run.py`).
- **Steps to reproduce:**
  1. Stop PostgreSQL (or point `POSTGRES_HOST` at a filtered port).
  2. `cd backend && alembic upgrade head`
  3. Observe: no prompt, no error, no output, indefinitely. Ctrl-C is the only exit.
- **Expected:** bounded by the same budget as the health probe, then a clear message naming `POSTGRES_HOST`/`POSTGRES_PORT` — the same remedy `wait_for_db.py` prints.
- **Actual:** an unbounded silent hang.
- **Impact:** the first thing a newcomer's local setup does after creating the venv cannot be completed and gives no clue why. The README's own troubleshooting section acknowledges hangs on this path ("### The application hangs on startup instead of failing") but only for the `localhost`-vs-`127.0.0.1` cause, and never mentions that the migration step has no timeout at all.
- **Suggested fix:** in `env.py`, add `connect_args={"connect_timeout": <seconds>}` (or wrap `connectable.connect()` in `asyncio.timeout`) and print a short remedy on failure before re-raising. Add a `--connect-timeout` CLI knob rather than reusing `DB_PROBE_TIMEOUT_SECONDS`, since migrating is not a probe. Trade-off: a genuinely slow migration must not be killed, so bound the *connect*, not `run_migrations`.

---

### P01A-04 — S2 — With the database down, the UI says "the backend hit an unexpected error"; nothing anywhere mentions the database, and the card takes ~12 s to say so

- **Component:** `frontend/src/pages/dashboard-page.tsx` (`HealthCard`, `dbConnected`/`degraded` branches), `frontend/src/components/feedback/error-state.tsx` (`describe`, default 5xx branch), `frontend/src/app/query-client.ts:16-21`
- **Category:** error-handling / ui-ux
- **Evidence:** a temporary vitest probe (`frontend/src/__probe.p01a.probe.test.tsx`, run with `npx vitest run`, then **deleted** — verified gone with `ls`) fed the *verbatim* body observed in run #5 into the real `ErrorState` / real `useHealth`:

  ```
  VISIBLE TEXT >>> The backend hit an unexpected errorThe failure was recorded on the
  server. Retry, and quote the request ID below.An internal server error occurred.
  Request ID dd258245-da6f-440d-8f09-56edbe79869bRetry

  HEALTH ERROR >>> { "status": 500, "code": "internal_error", "details": null,
                      "requestId": "dd258245-da6f-440d-8f09-56edbe79869b", "name": "ApiError" }
  ```

  Not one of those strings contains the word "database". Meanwhile the backend
  *has* a rendered branch for exactly this — `HealthCard` reads
  `data.status === 'degraded'` and renders a `warning` badge plus
  `Database: unavailable` — and it is unreachable because P01A-01 turns the
  degraded case into a 500.

  Timing, derived from measured parts (not measured in a browser): the probe
  takes 3.0 s (run #6), `query-client.ts:16-21` retries any status ≥ 500 twice
  (`failureCount < 2`) with React Query's default exponential backoff (1 s, 2 s),
  and `api-client.ts` allows 30 s per request. 3 + 1 + 3 + 2 + 3 ≈ **12 s**
  before the card renders an error, then it re-polls every 30 s.
- **Steps to reproduce:**
  1. Backend running, PostgreSQL stopped.
  2. Sign in, open the Dashboard.
  3. The "Backend health" card sits skeleton for ~12 s, then reads "The backend hit an unexpected error / An internal server error occurred."
- **Expected:** a degraded verdict naming the database, promptly — that is what the card's own `degraded` branch is for.
- **Actual:** a generic server-fault message, after ~12 s, with the database named nowhere.
- **Impact:** The single most common first-run failure of this application is reported as "something unexpected happened on the server". A newcomer's correct next step — start PostgreSQL — is not suggested anywhere on screen, and the request id they are told to quote points at a health check rather than at their environment.
- **Suggested fix:** Two independent fixes; do both. (1) P01A-01 restores the `degraded` 200 and the existing card branch works again. (2) Regardless, give `ErrorState` (or the health card specifically) a case for the transport/DB outage — a `503`-ish or health-probe failure should read "NEXUS is running but cannot reach its database. Start PostgreSQL and check POSTGRES_HOST / POSTGRES_PORT", which is the message `scripts/wait_for_db.py` already prints correctly. The generic 5xx copy should stay the fallback for genuine faults. Also consider `retry: false` on the health query specifically: it is a status display, not a data fetch, and three 3-second attempts to learn "still down" is a poor trade.

---

### P01A-05 — S3 — `RATE_LIMIT_CREDENTIAL_MAX_REQUESTS` is documented as `120` in three places; the code default is `180`, and all three omit the `/auth/register` bucket

- **Component:** `backend/app/core/config.py:123` vs `README.md:848`, `docs/development.md:1497`, `docs/development.md:1506`, `.env.example:108`
- **Category:** docs
- **Evidence:**

  ```
  rate_limit_credential_max_requests = 180        # printed from get_settings()
  ```
  ```
  README.md:848        RATE_LIMIT_CREDENTIAL_MAX_REQUESTS (120, for /auth/login and /auth/password/forgot)
  docs/development.md:1497  | RATE_LIMIT_CREDENTIAL_MAX_REQUESTS | `120` | The same budget for /auth/login and /auth/password/forgot
  docs/development.md:1506  120 a minute is two attempts a second, where a single bcrypt-12 ...
  .env.example:108     RATE_LIMIT_CREDENTIAL_MAX_REQUESTS=120
  ```
  `config.py:120-123` states the opposite in prose: *"180 a minute is three
  attempts a second, which is below the ~4/s a single bcrypt-12 verification
  already permits"*. All three documents also describe only **two** tight routes;
  `middleware.py:111` adds a third, `/auth/register`, on its own `account` bucket
  at the same limit, and `README.md:470-476` documents that bucket's *existence*
  without it appearing in any settings table.

  Because `.env.example` actively *assigns* 120, a newcomer who follows the
  documented first step (`cp .env.example .env`) gets 120, not the 180 the code's
  own comment reasons about — and no document tells them the two differ.

  `tests/test_documentation_claims.py:501`
  (`test_the_rate_limiter_is_documented_as_shipped_and_its_settings_are_written_down`)
  asserts only that the *names* appear in `.env.example` and `README.md`. I ran
  it: it passes.
- **Steps to reproduce:**
  1. `grep -n RATE_LIMIT_CREDENTIAL_MAX_REQUESTS README.md docs/development.md .env.example`
  2. `cd backend && python -c "from app.core.config import get_settings; print(get_settings().rate_limit_credential_max_requests)"`
  3. Compare: 120 in three documents, 180 in code.
- **Expected:** one number, everywhere, matching the code.
- **Actual:** two numbers, and the one a newcomer actually applies (`.env.example`) is the one the code's reasoning does not describe.
- **Impact:** an operator tuning the credential throttle reasons from the wrong figure, and the `/auth/register` account bucket — added specifically because register was "an account-enumeration oracle and a way to buy a quarter of a second of server CPU per call" (`middleware.py:97-110`) — is invisible in every settings table.
- **Suggested fix:** decide which number is intended (the code comment's 180 with its justification reads as the deliberate choice), then correct `README.md:848`, `docs/development.md:1497`, `docs/development.md:1506` and `.env.example:108`, and add the `/auth/register` account bucket to the same tables. Extend `test_documentation_claims.py` to assert the *default value*, not just the name — a presence check cannot catch a wrong number, which is exactly what happened three times.

---

### P01A-06 — S3 — Three different published counts for the API surface; none of them is the real one

- **Component:** `README.md:110`, `README.md:1042`, `README.md:570`, `docs/api-conventions.md:10`
- **Category:** docs
- **Evidence:**

  ```
  $ python -c "from app.main import app; s=app.openapi(); print(len(s['paths']),
      sum(1 for p in s['paths'].values() for m in p if m in ('get','post','put','patch','delete')))"
  paths: 145
  operations: 192
  ```
  | Where | Claimed |
  | --- | --- |
  | `README.md:110` | 140 paths, 187 operations |
  | `README.md:1042` | 140 paths, 187 operations |
  | `docs/api-conventions.md:10` | 142 paths, 189 operations |
  | **Reality** | **145 paths, 192 operations** |

  `docs/api-conventions.md:1560-1564` explicitly claims these figures *are*
  re-derived from `app.openapi()` ("the live schema had **140 paths and 187
  operations** when this pass landed, and Phase 11 has since taken it to 142 and
  189"). `README.md:570` likewise claims "19 routers"; `ls backend/app/api/v1/`
  shows 22 router modules (the 19 listed plus `search`, `ml`, and `actions`).

  I ran `pytest tests/test_documentation_claims.py -m "not integration"`:
  `14 passed, 3 deselected`. It passes because it bans a fixed list of
  *superseded phrases* (`tests/test_documentation_claims.py:96-107`) rather than
  deriving the current number — and "140 paths and 187 operations" is not on
  that list, because it was true when the list was written.
- **Steps to reproduce:**
  1. Run the OpenAPI dump above.
  2. `grep -n "paths and" README.md docs/api-conventions.md`
  3. Three answers, none of them 145/192.
- **Expected:** one number, derived from the app, matching across documents.
- **Actual:** three numbers, matching nothing.
- **Impact:** Low severity, real cost. `docs/api-conventions.md` tells readers to treat these as audited figures, and its own remediation history shows a reader will trust a wrong one and file a false "missing route" report. It is also the first thing a newcomer cross-checking the docs against reality will find wrong, which costs trust in everything else they read.
- **Suggested fix:** replace the literal with a generated figure (a `make` target that prints it, or a test asserting the strings match `app.openapi()`), and add `"140 paths and 187 operations"` / `"142 paths and 189 operations"` to `SUPERSEDED_CLAIMS` so the ban-list mechanism catches this class next time instead of freezing the stale value into the ban-list.

---

### P01A-07 — S3 — A malformed JSON body produces a validation error addressed to a field named `"1"`

- **Component:** `backend/app/core/exceptions.py:196-201`
- **Category:** contract / correctness
- **Evidence:**

  ```
  == malformed json -> 422
  {"error":{"code":"validation_error","message":"The request body or query parameters failed validation.",
    "details":{"errors":[{"field":"1","message":"JSON decode error","type":"json_invalid","context":{}}]},
    "request_id":"07506f51-3101-4e38-9fce-a225d63de5d"}}
  ```

  Pydantic reports this error with `loc = ("body", 1)`. Line 196 drops the
  `"body"` element and keeps the index, and line 198 joins what is left, so the
  field address becomes the string `"1"` rather than `"body"` or `"body.1"`. Every
  other body error compares correctly:

  ```
  == 422 validation -> 422
  {"error":{... "details":{"errors":[{"field":"email","message":"String should match pattern '...'"...
  ```
  The documented shape is a dotted path; `"1"` is not one. The frontend's
  `fieldErrorMessages` (`frontend/src/services/errors.ts`) looks each `field` up
  against real form inputs, finds none, drops it, and — because
  `bannerError` then sees no field errors — falls back to a page-level banner.
  So the user-visible effect is small; the wire contract is wrong.
- **Steps to reproduce:**
  1. `curl -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' -d '{not json'`
  2. Read `error.details.errors[0].field`.
- **Expected:** `"body"` (or `"body.1"`), i.e. something a caller can route to an input.
- **Actual:** `"1"`.
- **Impact:** A client generating form-level errors from `field` will look for an input named `1` and find nothing. Any consumer that assumes `field` is a path will mis-handle this one error type.
- **Suggested fix:** in `_validation_details`, drop a trailing integer index too, or special-case `json_invalid` to report `"body"`. One line: treat `location` as empty when the remaining parts are all integers. Trade-off: for a genuinely indexed body (`items[0].name`) the index is useful — so the narrow fix is to map `json_invalid`/`json_type` errors to `"body"` rather than to strip indices generally.

---

### P01A-08 — S3 — 404 and 405 answers are Starlette's bare words while every domain error has a written sentence

- **Component:** `backend/app/core/exceptions.py:299-301`
- **Category:** error-handling / ui-ux
- **Evidence:**

  ```
  /api/health              -> 404 {"error":{"code":"not_found","message":"Not Found", ...}}
  /api/v1/                 -> 404 {"error":{"code":"not_found","message":"Not Found", ...}}
  POST /health             -> 405 {"error":{"code":"method_not_allowed","message":"Method Not Allowed", ...}}
  GET  /api/v1/tasks       -> 401 {"error":{"code":"unauthorized","message":"Authentication credentials were not provided.", ...}}
  ```

  The envelope is correct in every case — `code`, `details`, `request_id`,
  `X-Request-ID` all present, and `WWW-Authenticate: Bearer` on the 401. The
  *messages* are not: for a routing miss the message is Starlette's
  `HTTPException.detail` default, `"Not Found"`, echoed straight through line
  300. Compare `NexusError.default_message` in the same file
  (`exceptions.py:114`: `"The requested resource was not found."`) — every
  deliberate error in NEXUS has a sentence written for it, and the two most
  common mistakes a newcomer makes (wrong prefix, trailing path) get the raw
  framework word.
- **Steps to reproduce:**
  1. Start the server.
  2. `curl http://localhost:8000/api/health` (forgot the `/v1`).
  3. The answer is `Not Found` — identical for a typo, a missing version prefix, and a route that does not exist.
- **Expected:** something that helps — e.g. "No such endpoint. The versioned API is mounted at /api/v1; see / for the endpoint index."
- **Actual:** `Not Found`.
- **Impact:** No functional impact; real onboarding cost. A newcomer who guesses the prefix wrong gets no signal at all, and the app has a `/` endpoint that lists exactly this information. Note the rate limiter shares the blind spot: `middleware.py:368-372` documents that the limiter keys on the concrete path, so `/api/health` and `/api/v1/health` draw separate budgets.
- **Suggested fix:** in `_handle_http_exception`, special-case 404 and 405 to use NEXUS's own sentences rather than `exc.detail` — `"The requested resource was not found."` / `"That verb is not supported on this path."` Keep echoing `detail` for the codes where it is application-authored. Optionally add the API prefix to the 404 message when the request path is under the configured prefix.

---

### P01A-09 — S4 — `migrations/README.md` describes a loop selection the code no longer uses, and gives a `localhost` example it elsewhere warns against

- **Component:** `backend/migrations/README.md:94`, `backend/migrations/README.md:66`, `backend/alembic.ini:13`
- **Category:** docs
- **Evidence:** `migrations/README.md:94` reads

  > "`env.py` passes `loop_factory=asyncio.SelectorEventLoop` on `win32` so `alembic` works out of the box."

  `backend/migrations/env.py:124` in fact reads
  `asyncio.run(run_async_migrations(), loop_factory=nexus_loop_factory)`, and
  `app/core/event_loop.py:33-36` selects the loop on *every* platform, not only
  `win32`. `README.md:1132-1134` states the current design correctly ("selected
  in exactly one place … applied by `backend/run.py`, by `migrations/env.py` and
  by `tests/conftest.py`"), so this file is the stale copy.

  `migrations/README.md:66` and `alembic.ini:13` both show
  `DATABASE_URL=postgresql+psycopg://nexus:nexus@localhost:5432/nexus_test` —
  the exact value `migrations/README.md:96-100` and `README.md:912-918` warn
  causes psycopg's async connect to hang on Windows, with no connect timeout to
  bound it (P01A-03).
- **Steps to reproduce:** read the three cited lines.
- **Expected:** the migration README describes the code that exists and its examples do not contradict its own warnings.
- **Actual:** it describes an older `env.py` and offers a `localhost` DSN that, per the same file, hangs.
- **Impact:** Minimal — a reader who follows it lands on the Windows hang documented in P01A-03, which is at least discoverable.
- **Suggested fix:** reword `migrations/README.md:94` to "delegates to `app.core.event_loop.nexus_loop_factory`, which selects a `SelectorEventLoop` on every platform", and change the two example DSNs to `127.0.0.1`.

---

## Checked and found correct

Actively exercised; do not re-investigate these.

- **The app boots and serves.** `python run.py` on a real uvicorn (port 8931): `/` → 200 with the full service-metadata body and its `links` index; `/docs` → 200 Swagger; `/redoc` → 200 ReDoc; `/openapi.json` → 200; `/health` → 200 `{"status":"ok"}` without touching the database. `GET /api/v1/health/` correctly 307-redirects.
- **Liveness is genuinely independent of the database.** `tests/test_health.py:20` monkeypatches every DB entry point to raise; `/health` still answers 200. I confirmed it independently — `/health` returns 200 while every DB-backed path fails.
- **`X-Request-ID` correlation is complete.** Present on 200s, 404s, 405s, 422s, 429s and the 500 (that last one matters and is deliberate — `middleware.py:527-541` installs the layer above `ServerErrorMiddleware` precisely so the catch-all 500 still carries the header). The id in the header always equals the `request_id` in the error body. An inbound `X-Request-ID` is echoed rather than replaced, as documented.
- **No header injection via the inbound request id.** I sent `X-Request-ID: abc\r\nX-Injected: pwned` over a raw socket to a live uvicorn. Response came back `x-request-id: abc` and no injected header — the ASGI parser rejects it before the app sees it.
- **Secret redaction works in both log paths.** With `LOG_REQUEST_BODY=true`: body logged as `{"email": "a@b.co", "password": "***redacted***"}`; query logged as `access_token=***redacted***&limit=5`. `current_password`, `token`, `secret`, `authorization` and friends are in `REDACTED_KEYS` (`logging.py:51-79`). The non-JSON body formats are summarised by size rather than quoted (`middleware.py:517-519`), which is the right call.
- **Rate limiting is correct and well-behaved.** With `general_max_requests=3`: three 200s then `429` with `Retry-After: 60` and the shared envelope. The denied request is *not* counted, so the budget genuinely returns. `X-Forwarded-For` is correctly ignored by the limiter (`rate_limit_trust_forwarded_for=False`) — requests from `10.0.0.9` still shared `127.0.0.1`'s exhausted budget. `OPTIONS` is exempt.
- **CORS.** Allowed origin `http://localhost:5173` → `access-control-allow-origin` echoed; hostile origin `https://evil.example` → no ACAO header; preflight returns the allowed methods, `allow-credentials: true`, `max-age: 600` and the requested headers. Defaults in `config.py:139` match the Vite proxy origins in `vite.config.ts:76-85`, and the proxy covers both `/api` and `/health`.
- **The error envelope is uniform.** 404, 405, 422, 401 and 429 all return `{"error":{code, message, details, request_id}}` with the right `code` for the status, `details: null` where there is nothing structured, `WWW-Authenticate: Bearer` on the 401, and `Retry-After` on the 429. `_status_code_to_code` correctly reserves `internal_error` for 5xx. 5xx messages never echo the originating exception (`exceptions.py:281-298`) — verified: the deliberate-failure message from the observability test never appears in the body.
- **The offline migration pipeline works.** `alembic heads` → `0011 (head)`; `alembic history` → a clean linear chain `<base> → 0001 … → 0011`, no branches; `alembic upgrade head --sql` → exit 0, 886 lines of SQL, eleven `Running upgrade` lines, no database needed. `alembic.ini` ships with an empty `sqlalchemy.url` and no credentials; `env.py:48-53` injects the URL from the settings singleton and lets an explicit env override win.
- **Config refuses to start on nonsense.** `ENVIRONMENT=production` with the placeholder `SECRET_KEY` or with `DEBUG=true` raises; the four productivity weights must sum to 100 or `Settings` refuses to construct; `ML_CONFIDENCE_THRESHOLD` outside `(0,1]`, an unknown `ML_DEVICE`, and a non-positive or absurd `ML_MAX_INPUT_CHARS` all raise. `ml_model_path` is resolved relative to the repository and never hard-codes a machine path.
- **`scripts/bootstrap.py --skip-install`** exits 0, correctly detects Docker's absence, refuses to overwrite an existing `.env`, and prints accurate next steps for both the Compose and the local-PostgreSQL path.
- **`scripts/wait_for_db.py --timeout 6`** exits 1 with a classified diagnosis ("no PostgreSQL server answered on that host and port") and a redacted DSN. This is the *right* behaviour and the benchmark the health endpoint should have met (see P01A-01/P01A-04).
- **The frontend error surface is well built apart from P01A-04.** `ErrorState` uses `role="alert"`, renders the backend's user-safe message plus the request id and never a stack trace or a raw body, and the Retry button has visible text. `toApiError` normalises aborts and unknown throws. `bannerError` correctly suppresses a banner when every 422 message is already shown inline against a field.

## Out-of-phase observations

- `frontend/src/types/api.ts:186-194` — `ApiErrorCode` lists 6 of the 10 codes in `app/core/exceptions.py`; `bad_request`, `method_not_allowed` and `ml_unavailable` are absent from the union. The `(string & {})` member makes this non-exhaustive rather than wrong, and it is a deliberate widening. Noting it only because P01A-02 makes those three reachable from Swagger for the first time.
- `README.md:65` states the backend suite is "**3 588 backend passed**" while `README.md:650`, `:943` and `:979` state "2270 collected" and `README.md:261` states "2591 passed, 1 skipped"; line 65 also says "58 frontend test files, 876 passing" while lines 72-74 say "52 files … 799 … all green". `test_every_document_that_states_a_suite_count_states_the_same_one` (`tests/test_documentation_claims.py:572`) passes because its regex `(\d{3,4}) tests? in (\d{2,3}) files?` does not match the `799, all green` phrasing — same class as P01A-06, and I did not measure any suite count (HARNESS §3.2 forbids it).
- `backend/tests/test_health.py:68` passes while the readiness contract is broken (P01A-01) because it patches the probe to *return* `False`. Worth a general note for whoever owns the test suite: several Phase 1 tests stub the very function whose real failure mode is the defect.

**Capabilities I could not exercise in this environment** (HARNESS §1): no PostgreSQL and no Docker, so I never observed a *successful* `alembic upgrade head` against a real schema, a `database.status: "connected"` health body, `pg_trgm`/`unaccent` extension state, or any DB-backed route. No browser, so the ~12 s health-card timing in P01A-04 is derived from measured backend timings plus the retry config, not observed. ML checkpoint loading was run once by the lifespan (`ml_checkpoint_exists=True` on this host, torch emitted a `torch.jit.script` deprecation `FutureWarning` during weight load) but I did not exercise Phase 11 endpoints.