# NEXUS — Development guide

The rules for writing new code in NEXUS: how to get from a clean machine to a
running stack, what a normal edit-run-test cycle looks like, and — the part the
other documents deliberately do not cover — **the conventions a new endpoint,
page, migration or test has to follow.**

Everything here was read from the repository. Where a number appears, it came
from a command that was run; where something could not be run, it says so.

---

## How this fits with the other documents

Four documents, four non-overlapping jobs. If you are looking for something and
it is not here, it is in one of the other three.

| Document | Owns | Does not own |
| --- | --- | --- |
| [`../README.md`](../README.md) | Installation, environment variables, the command catalogue, troubleshooting | Conventions — *why* code is shaped the way it is |
| [`architecture.md`](architecture.md) | Structure and rationale: layering, request lifecycle, auth design, persistence, decisions | Step-by-step "how do I add X" procedures |
| **`development.md`** (this file) | Setup on a clean machine, the daily loop, adding an endpoint / page / migration / test, style and code conventions | Wire contract details, environment variables, troubleshooting |
| [`api-conventions.md`](api-conventions.md) | The endpoint contract: versioning, error envelope and codes, request ids, pagination, the endpoint checklist | Internal layering — which file raises the error |

Two consequences worth stating plainly:

- **The endpoint checklist is not repeated here.** `api-conventions.md` owns the
  wire contract — status codes, error shapes, auth requirements per route. This
  document owns the *code* contract: which layer a line of logic goes in, and
  which layer is allowed to know about what.
- **Troubleshooting is not repeated here.** If something is broken at runtime,
  the README's [Troubleshooting](../README.md#troubleshooting) section owns the
  diagnosis. This document tells you how not to break it.

---

## Contents

| Section | Contents |
| --- | --- |
| [1. Clean-machine setup](#1-clean-machine-setup) | Prerequisite checks, the bootstrap script, the database, verifying the install |
| [2. The daily loop](#2-the-daily-loop) | Edit → run → test, hot reload, what to run when |
| [3. Adding a backend endpoint](#3-adding-a-backend-endpoint) | The layering rule, a repository and a service, a permission-guarded route, error translation, audit events, the vertical slice |
| [4. Migrations](#4-migrations) | autogenerate → check → apply, and the rules about revision files |
| [5. Adding a frontend page](#5-adding-a-frontend-page) | The three-file pattern, the registry contract, a settings panel, real copy vs. sample data |
| [6. Testing conventions](#6-testing-conventions) | `integration` marker, fixtures, `assert_error_envelope`, the regression rule |
| [7. Design-system rules](#7-design-system-rules) | Tokens, `cva` variants, the Radix-backed and hand-rolled primitives, what not to build by hand |
| [8. Code conventions](#8-code-conventions) | ruff, docstrings, TypeScript strictness, the react-refresh constraint |
| [9. Before you open a pull request](#9-before-you-open-a-pull-request) | The checklist |
| [10. Verified baseline and known limits](#10-verified-baseline-and-known-limits) | What was actually executed, and what was not |
| [11. Developer, Learning, Career and ML settings](#11-developer-learning-career-and-ml-settings) | Registering and scanning a local repository, the fourteen environment variables Phases 8 and 9 added, rate limiting, the Phase 4 / Phase 6 settings, and the seven `ML_*` settings Phase 11 added (§11.6) |

---

## 1. Clean-machine setup

### 1.1 What you need

| Requirement | Version | Used by |
| --- | --- | --- |
| Python | 3.13+ | backend, migrations, tests, scripts |
| Node.js | 20.19+ (22 LTS recommended) | frontend dev server, tests, build |
| npm | ships with Node | frontend |
| PostgreSQL | 16 (13+ works) | the backend, the integration tests |
| Docker + Compose v2 | recent | optional — only for the `make up` path |
| GNU make | 4.x | optional — the Makefile wraps nothing the scripts and npm do not already do |

`make` is not shipped with Windows. Run the raw commands instead; the README's
[Development commands](../README.md#development-commands) table has all of them.

**Phase 11 added a dependency the rest of the backend does not need.** `torch` and
`transformers` are now pinned in `backend/requirements.txt` and are installed like any
other runtime dependency — with the caveat in [§1.2](#12-bootstrap) about the index URL.
They are still imported *lazily*, so an environment without them boots, serves every other
route, and answers the two `/ml` endpoints with 503 `ml_unavailable`; a developer who only
wants the API surface can leave them out. A developer who wants to run
`pytest tests/test_ml_integration_*.py`, or `POST /api/v1/ml/route` against a real model,
needs both **and** the trained checkpoint from Phase 10 under `backend/ml/artifacts/` —
which is gitignored, so a fresh clone has neither.

### 1.2 Bootstrap

From the repository root:

```bash
python scripts/bootstrap.py                # everything
python scripts/bootstrap.py --skip-install # checks and .env only
```

The script is stdlib-only, so it runs on a bare interpreter — it has to, because
it is what creates the environment everything else depends on. In order
(`scripts/bootstrap.py`):

| Step | Function | Failure mode |
| --- | --- | --- |
| Python ≥ 3.13 | `check_python` | fatal |
| Node ≥ 20 and `npm` on PATH | `check_node` | fatal |
| Checkout layout (`backend/requirements.txt`, `backend/alembic.ini`, `frontend/package.json`) | `check_repository` | fatal |
| `.env` from `.env.example` | `ensure_env_file` | never overwrites an existing `.env` |
| `backend/.venv` | `ensure_venv` | reuses an existing virtualenv |
| `pip install -r backend/requirements.txt` | `install_backend` | fatal, names proxy/network as the usual cause. **See the known gap below: this command needs `--extra-index-url` for the Phase 11 torch pin** |
| `npm install` in `frontend/` | `install_frontend` | fatal; warns if `package-lock.json` is missing |
| Docker availability | `report_docker` | **never fatal** — the stack also runs natively |

It is idempotent: re-running it is the correct response to almost any setup
problem.

**What it deliberately does not do:** it does not start PostgreSQL, create
`nexus_test`, run migrations, or start any server. Those are the next three
steps, in that order.

#### The ML stack needs a second index, and the bootstrap does not pass it

**Known gap, found while writing this section and not yet fixed.** Phase 11 made
`torch==2.14.1+cpu` and `transformers==5.18.0` runtime dependencies: the trained classifier
now runs *inside* the API process, so they belong above the `DEV MARKER` in
`backend/requirements.txt` and are baked into the runtime image. The `+cpu` suffix is a
**local version identifier published only on the PyTorch CPU index**, so a clean install
must name that index:

```bash
# from the repository root, after bootstrap has created backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cpu
```

Two places do not pass it today, and both fail the same way:

| Location | Command as shipped | Consequence |
| --- | --- | --- |
| `scripts/bootstrap.py:157-160` | `python -m pip install -r backend/requirements.txt` | `pip` reports *"No matching distribution found for torch==2.14.1+cpu"* and `install_backend` aborts as fatal |
| `Makefile:80-81` (`install`, aliased by `bootstrap`) | runs `$(PYTHON_BOOTSTRAP) scripts/bootstrap.py` | inherits the failure above — `make install` does not currently produce a working ML environment |
| `backend/Dockerfile:26-29` | awk-extracts the runtime section, then `pip install -r requirements.runtime.txt` | the same failure at image build time; `requirements.txt` records this as its own known gap |

Both call sites are one flag away from correct, and neither has been changed here because
this document does not edit the installer or the build. Until they are, install the
requirements yourself with `--extra-index-url` after a bootstrap run — or after a failed
one, since the virtualenv and `.env` are already in place and the script is idempotent.

The failure is loud rather than silent, which is the only reason it is a bug and not a
mystery: pip will not quietly install the CUDA build of a different version and leave you
with a classifier whose numerics have silently moved away from the ones the 0.9738 was
measured with.

### 1.3 The database, in the right order

```bash
# from the repository root — wait for the server, then create the test database
backend/.venv/bin/python scripts/wait_for_db.py
backend/.venv/bin/python scripts/create_test_database.py

# migrate — alembic always runs from backend/
cd backend && ../backend/.venv/bin/python -m alembic upgrade head
```

On Windows the interpreter is `backend/.venv/Scripts/python.exe`.

`create_test_database.py` refuses to run if `TEST_DATABASE_URL` resolves to the
same database as `DATABASE_URL`, because the test suite truncates every managed
table. `tests/conftest.py` repeats that check (`_assert_separate_test_database`)
and aborts the session with `pytest.exit` if it ever trips — the script is
optional, the conftest is not.

You can skip this step entirely if you are using the Docker path: the backend
container runs `alembic upgrade head` on start, and `tests/conftest.py` creates
`nexus_test` itself.

### 1.4 Run it

```bash
./scripts/dev.sh              # backend + frontend, Ctrl-C stops both
./scripts/dev.sh backend      # API only, :8000
./scripts/dev.sh frontend     # Vite only, :5173
```

Or in two terminals: `cd backend && python run.py` and `cd frontend && npm run dev`.

> **Start the backend with `python run.py` from `backend/`.** A bare
> `uvicorn app.main:app` starts on Windows and then fails on every query, because
> psycopg's async driver needs `loop.add_reader` and the default Windows
> `ProactorEventLoop` does not provide it. `run.py` selects the loop through
> `app.core.event_loop.nexus_loop_factory`, which is also what
> `migrations/env.py` and `tests/conftest.py` use. There are exactly three
> consumers of that factory and a fourth would be a bug.

### 1.5 Verify the install

Run these before your first change. Every one of them is the same command the
pre-pull-request checklist asks for, so a green run here means you know what
"green" looks like.

```bash
# backend — from backend/
PY=../.venv/bin/python          # Windows: PY=../.venv/Scripts/python.exe
$PY -m ruff check .
$PY -m ruff format --check .
$PY -m pytest -m "not integration"

# frontend — from frontend/
npm run typecheck
npm run lint
npm test
npm run build
```

Then confirm the app, not just the tooling:

| Check | Where | Expected |
| --- | --- | --- |
| Liveness | `http://localhost:8000/health` | `{"status":"ok"}` — no database needed |
| Readiness | `http://localhost:8000/api/v1/health` | `status: "healthy"`, `database.status: "connected"` |
| UI | `http://localhost:5173` | Sign-in page; register an account from `/register` |

If `/health` is green but readiness says `degraded`, the backend is fine and the
database is not — that is the documented split, and the README's
[troubleshooting section](../README.md#the-database-is-unreachable) has the fix.

---

## 2. The daily loop

### 2.1 The cycle

| Stage | Command | Where |
| --- | --- | --- |
| Start both servers | `./scripts/dev.sh` | repository root |
| Backend hot reload | automatic, with `NEXUS_RELOAD=true` in `.env` | — |
| Frontend hot reload | automatic | — |
| Backend checks | `python -m ruff check . && python -m ruff format --check .` | `backend/` |
| Frontend checks | `npm run typecheck && npm run lint` | `frontend/` |
| Backend tests | `python -m pytest -m "not integration"` (fast) / `python -m pytest` (full) | `backend/` |
| Frontend tests | `npm test` | `frontend/` |

Throughout this document `python` means the project interpreter — `backend/.venv/bin/python`,
or `backend\.venv\Scripts\python.exe` on Windows — not whatever happens to be on `PATH`.

`NEXUS_RELOAD` defaults to `false` in `run.py`, so **set it to `true` for local
work** or you will be restarting the server after every edit. Compose forces it
to `false`, which is correct there: reload forks a second process, and a second
worker carries its own connection pool and its own empty revocation denylist.

### 2.2 Choosing the test command

| You changed | Run |
| --- | --- |
| Python, no schema or data | `python -m pytest -m "not integration"` — 1029 tests, no database |
| A model, a repository, or anything touching data | `python -m pytest` — the full 2270, needs PostgreSQL |
| `app/ml/`, or the `/ml` routes | `python -m pytest tests/test_ml_integration_*.py` — needs the trained checkpoint and torch, and skips cleanly without either (§6.1) |
| TypeScript | `npm run typecheck && npm test` |
| A component's markup or a route | `npm test`, plus `npm run build` — `tsc -b` catches types and import paths, but only a real build proves the module graph resolves |

The `-m "not integration"` subset **must pass with PostgreSQL stopped.** That is
the point of the marker; if a test that could run offline has been marked
`integration` to make it pass, the suite has lost its most useful property.

### 2.3 Make targets

`make` is a thin wrapper — every target is a real command, and the logic lives in
`scripts/` and `package.json`. Use it if you have it:

```bash
make install     # scripts/bootstrap.py
make lint        # ruff check + ruff format --check + eslint + tsc -b
make test        # test-backend then test-frontend
make migrate     # alembic upgrade head
make backend     # scripts/dev.sh backend
```

On Windows, pass the interpreter explicitly — the Makefile default is the POSIX
layout:

```bash
make test-backend PY=backend/.venv/Scripts/python.exe
```

The full target list is in the
[README](../README.md#make-targets); `make help` prints it.

---

## 3. Adding a backend endpoint

Read the reference slice first. `backend/app/api/v1/auth.py` (router),
`backend/app/services/auth_service.py` (rules),
`backend/app/repositories/user.py` (SQL) and `backend/app/models/user.py` (table)
together are the only complete vertical slice in the repository, and a new module
should look like it. `app/api/v1/users.py` is the second one, and the smallest —
two routes over the caller — so it is the easier of the two to copy.

### 3.1 The layering rule

**Dependencies point downward only.**

| Layer | Package | May import | Must not import |
| --- | --- | --- | --- |
| HTTP | `app/api/v1/*.py`, `app/api/deps.py` | FastAPI, `app.schemas`, `app.services`, `app.core` | SQLAlchemy, ORM internals, `app.repositories` internals |
| Domain | `app/services/*.py` | `app.schemas`, `app.repositories`, `app.core` | **FastAPI, ever** |
| Data | `app/repositories/*.py` | SQLAlchemy, `app.models` | HTTP, domain errors |
| Infrastructure | `app/core/*`, `app/db/*`, `app/models/*` | below itself | services, routers |

The domain layer's FastAPI ban is stated in the module docstring of
`app/services/auth_service.py` and is what keeps business rules testable without
a request. It is not aspirational — a `from fastapi import …` inside a service is
a layering violation, not a style preference.

`app/schemas/` sits on the HTTP boundary and is the only place Pydantic
validation runs.

### 3.2 Where the wiring goes

Two dependency modules, and the split matters:

| Module | Owns | Import it from |
| --- | --- | --- |
| `app/core/deps.py` | Identity and authorisation: bearer scheme, `CurrentUser`, optional user, `SuperUser`, the `sid` claim, `require_permission()`, session → repository. Imports no service, which keeps the graph acyclic. | `app/api/deps.py` re-exports it |
| `app/api/deps.py` | Layering on top: `get_<module>_service` (session → repository → service), `get_authenticated_user` (which adds the revocation check), and `get_client_context(request)` → `(ip_address, user_agent)` | the routers |

A new module adds one provider here, mirroring `get_auth_service`:

```python
def get_project_service(repository: ProjectRepositoryDep) -> ProjectService:
    """Provide a request-scoped project service."""
    return ProjectService(repository)
```

Routers then depend on `Annotated[ProjectService, Depends(get_project_service)]`
and never construct a repository or a session themselves.

**`get_client_context` is how a router collects audit context.** Phase 2 added it
because the routers must not reach into the request themselves: the API layer hands
the services a plain `(ip_address, user_agent)` pair, and the services pass those to
`AuditService.record`. It is also the one place the truncation to the column widths
(45 and 512 characters) is applied, so no writer has to remember it. A new
security-relevant router should take `ClientContext` and forward it, not re-derive
the address.

### 3.3 A permission-guarded endpoint

Phase 2 added `app/core/permissions.py`. A protected route names the **capability**
it guards; it never names a role.

```python
from app.core.deps import get_current_active_superuser, require_permission
from app.core.permissions import Permission

@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the caller's profile",
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
async def update_me(payload: UserUpdate, current_user: AuthenticatedUser, …) -> User:
    ...
```

Rules that follow:

| Rule | Why |
| --- | --- |
| The route declares `Permission.X`, never `"admin"` | The role → permission map is the single place a grant is decided. A hard-coded role comparison is a second, drifting answer |
| The check runs *after* authentication | `require_permission` returns a dependency that depends on `get_current_user`, so an anonymous caller gets 401, never 403 — otherwise it could tell "not signed in" from "not permitted" |
| To use a capability you must add it to `ROLE_PERMISSIONS` | An unknown permission is treated as not held (`has_permission` returns `False` rather than raising), so declaring a route guard alone would deny everyone — including admins, since `ADMIN_ROLE` is `frozenset(Permission)` |
| `Permission` is a `StrEnum`; its value is the stable string | It is persisted, returned in `UserRead.permissions`, and filtered on by clients. Never rename a value once it ships |
| An **administrator-only** surface takes both gates | `GET /api/v1/users/` requires `USERS_READ` *and* `Depends(get_current_active_superuser)`: the permission says the capability exists, the superuser check narrows it to something that is not part of the product surface |

**404, not 403, for a row the caller does not own.** If your endpoint acts on a
caller-scoped resource, scope the lookup by the caller's id in the query
(`get_by_id_for_user(session_id, user_id)`) and raise `NotFoundError`. A 403 would
confirm the id exists and turn the route into a probe for real ids.
`SessionService.revoke` is the reference implementation.

**Add a permission to a new module's catalog, not to a route.** `app/core/permissions.py`
declares **eleven** capabilities today — `users.read/write`, `projects.read/write`,
`tasks.read/write`, `analytics.read`, `calendar.read/write` and `knowledge.read/write` —
and every module router from Phase 3 onward guards its reads and its writes with one of
them. Phases 8 and 9 added none: Developer, Learning and Career are guarded by
`analytics.read`, because registering a repository or writing a goal is the caller
answering a question about rows derived from their own record. Before adding a capability,
read the argument in
[`api-conventions.md`](api-conventions.md#analyticsread-guards-the-writes-too) — a new
member is granted to exactly the roles an existing one already covers, and
`tests/test_permissions.py` asserts the complete set, so it will fail until both are
extended together.

### 3.4 The error-translation rule

Two halves, and both matter:

| Layer | Rule | Reference |
| --- | --- | --- |
| Repository | **Never raises a domain error.** Returns `None` for a miss; an unexpected `IntegrityError` propagates untouched. | `UserRepository.get_by_id` returns `User \| None`; `app/repositories/user.py` states the rule in its module docstring |
| Service | **The single translation point.** Catches the raw driver error and raises the domain error the API contract promises. | `AuthService.register` catches `IntegrityError` and raises `ConflictError` |

Why it is drawn this way: one place decides that a duplicate e-mail is `409`
rather than a 500. If repositories translated too, the mapping would exist in
every module and drift. The cost accepted is that services must be prepared for
raw database exceptions — that trade is recorded in
[`architecture.md` §16](architecture.md#16-design-decisions).

Corollary: **a router raises nothing.** Routers pick a success status and return.
`NotFoundError`, `ConflictError`, `ForbiddenError` and `UnauthorizedError` come
out of the service; `install_exception_handlers` in `app/core/exceptions.py`
renders them. Never construct an error-shaped `JSONResponse` by hand.

### 3.5 Writing a repository and a service

Phase 2 added three repositories and two services, and the shape did not change.

**Repository — SQL only, no domain errors.** The reference is
`app/repositories/session.py`.

| Rule | Detail |
| --- | --- |
| Return `None` for a miss | `get_by_id`, `get_by_id_for_user`, `get_by_token_hash` all return `T \| None`. Unexpected driver errors propagate untouched |
| Scope by the owner when the row is caller-scoped | `get_by_id_for_user(session_id, user_id)` filters on both in the query. This is what makes 404-not-403 possible without an ownership check afterwards |
| Commit, then `refresh()` | `expire_on_commit=False` means a create can return populated attributes, but `created_at` / `updated_at` come from server defaults and still need a reload |
| Expose intent, not SQL | `revoke_all_for_user`, `list_live_ordered_by_created`, `rotate_token` are methods. A router or service that has to know the ordering is a leak |
| The module docstring states the surface | `session_service.py` lists the exact repository API it relies on, which is what makes the repository safe to refactor |

**Service — the rules, and no FastAPI.** The references are `SessionService` and
`UserService`.

| Rule | Detail |
| --- | --- |
| Raise a domain error for every failure mode | Never return `None` to mean "not found" from a public method |
| Normalise identifiers here | `_clean_username` trims without folding case; the uniqueness lookup then compares exactly what the column will hold |
| Translate `IntegrityError` → `ConflictError` | On **both** the pre-check and the commit, so a race cannot leak a driver error. `AuthService._conflict_message` reads the constraint name so a username collision gets the username message |
| Collapse equivalent failures into one message | Every rotation rejection answers "This session is no longer valid." A caller that can distinguish "signed out" from "already rotated" has learned something |
| Take an optional `AuditService` and use it if present | `self.audit = audit` may be `None`, so the service can be exercised without an audit sink; production wires one |

### 3.6 Vertical-slice checklist

Order matters only in that the schema must exist before autogenerate sees the
model. Every item is a real file in the repository or a real command.

| # | Step | File or command |
| --- | --- | --- |
| 1 | Model: subclass `Base` with `UUIDPrimaryKeyMixin` and `TimestampMixin` | `app/models/<module>.py` |
| 2 | Migration | see [§4](#4-migrations) |
| 3 | Repository: SQL only, `None` on a miss, commit, then `refresh()` | `app/repositories/<module>.py` |
| 4 | Schemas: `<X>Create`, `<X>Update`, `<X>Read` with `model_config = ConfigDict(from_attributes=True)`; constraints here, so a bad payload is a 422 | `app/schemas/<module>.py` |
| 5 | Service: raise a domain error for every failure mode; normalise input here | `app/services/<module>_service.py` |
| 6 | Provider: `get_<module>_service` | `app/api/deps.py` |
| 7 | Router: `APIRouter(prefix="/<module>", tags=["<module>"])`, `response_model`, explicit `status_code`, one-line `summary` | `app/api/v1/<module>.py` |
| 8 | Register the router | `app/api/v1/router.py` — `api_v1_router.include_router(<module>.router)` |
| 9 | **Permissions**: add `projects.<verb>` to `ROLE_PERMISSIONS`, and `Depends(require_permission(…))` to each guarded route | `app/core/permissions.py`, `app/api/v1/<module>.py` — see [§3.3](#33-a-permission-guarded-endpoint) |
| 10 | **Audit**: record the security-relevant events from the service, never the router | `app/services/audit_service.py` — see [§3.8](#38-recording-an-audit-event) |
| 11 | Tests | see [§6](#6-testing-conventions) |
| 12 | Frontend pairing: one function in `frontend/src/services/`, the wire type in `frontend/src/types/api.ts`, and a catalog entry if the page appears in the sidebar or palette | cross-link the endpoint checklist in [`api-conventions.md`](api-conventions.md#checklist-for-a-new-endpoint) |

Step 12 is the one most often forgotten, and the failure is a runtime mismatch
rather than a compile error — the backend schema and the TypeScript type are
checked by nothing except agreement.

### 3.7 The wire contract

Status codes, error envelopes, auth per route, pagination, request ids — all
owned by [`api-conventions.md`](api-conventions.md). Do not restate them here;
read that document's
[checklist for a new endpoint](api-conventions.md#checklist-for-a-new-endpoint)
before writing the route decorator, because it is more specific than anything
this document could say.

### 3.8 Recording an audit event

`AuditService.record()` is called from the **service**, never from the router —
that is what makes the same event recorded whether it came from an HTTP endpoint
or, later, from a background job.

```python
await self.audit.record(
    AuditEvent.SESSION_REVOKED,
    user_id=user_id,
    ip_address=ip_address,
    user_agent=user_agent,
    metadata={"session_id": str(session_id)},
)
```

| Rule | Detail |
| --- | --- |
| Add a new `AuditEvent` member for a genuinely new event; do not reuse one | The values are persisted and are what consumers filter on. `validate_audit_event` rejects an unknown one rather than writing a row no filter can match |
| **`metadata` may never contain a password, a token, or a hash of either** | It is JSONB, written verbatim, rendered into exports, and retained longer than the sessions it describes. Nothing is filtered on the way in, because a redaction list eventually misses the one field that matters |
| Keep it minimal and non-secret | A session id, a username, a count of sessions ended. Not the values that were submitted |
| `user_id` may be `None` | A failed sign-in against an address with no account is exactly the event worth recording, and it has no user row. The column is nullable for that reason |
| **Never retry a failed write** | `record()` already swallowed the error and logged it at WARNING. A write that failed partway is not safe to assume did not happen, and re-recording a security event is how a trail stops being evidence |
| Do not branch on the return value | `record()` returns `AuditLog \| None`; the `None` is a diagnostic, not a condition the service should act on. Audit must not be able to deny service |
| Record `ACCOUNT_DELETED` **before** the row is deleted | `audit_logs.user_id` is `ON DELETE SET NULL`, so the trail survives — but only the rows already written |

The twelve current events are listed in `app/models/audit.py`. `audit_log_retention_days`
states a retention policy that **no job enforces**; if you add the job, it is a
background worker and it needs the same event-loop factory as everything else.

---

## 4. Migrations

[`backend/migrations/README.md`](../backend/migrations/README.md) is the
migration subsystem's own reference — command list, async-driver mechanics,
Windows notes. This section is the part a *change author* needs: the rules about
writing a revision rather than running one.

### 4.1 The workflow

All commands run from `backend/`.

```bash
# 1. edit the model
# 2. generate the revision from the model's metadata
python -m alembic revision --autogenerate -m "add widgets table"

# 3. read the generated file. Autogenerate is a first draft, not an answer.

# 4. prove there is no drift left
python -m alembic check

# 5. apply
python -m alembic upgrade head

# 6. roll back and forward again — a migration that only works going up is not done
python -m alembic downgrade -1
python -m alembic upgrade head
```

`python -m alembic check` exits non-zero when `Base.metadata` and the live schema
disagree. Note that it needs a reachable database; the same comparison is
asserted by `backend/tests/test_migrations.py::test_autogenerate_reports_no_drift`,
which is `integration`-marked and so only runs when one is available.

### 4.2 Rules for revision files

**A migration must not import a model.** `migrations/versions/0001_initial_create_users.py`
and `0002_phase2_identity_sessions.py` both say so in their own module docstring, and
every later revision follows the rule — and it is the single most important rule in this
section: a revision is explicit DDL that happens to look like what the model describes
today. If it imported `app.models.user`, then editing the model later would silently
change the *meaning* of an already-applied migration — and on a fresh database, or after
a `downgrade`, that old revision would produce a different schema than it did the day it
was written.

Corollary: autogenerate output is a **draft**. Rename a column in a generated
`op.alter_column`, add `server_default=`, or add a comment, and the drift check
will still be happy. What you write by hand is what the migration means.

Autogenerate compares metadata against a live database and is wrong often enough
that an unreviewed generated revision is worse than a hand-written one. Two
failure modes to watch for:

| Autogenerate cannot | It emits instead | So |
| --- | --- | --- |
| See a rename | drop + add | Write the rename yourself (`op.alter_column(..., new_column_name=…)`) or the data is lost |
| Infer a data backfill | nothing | Write the `op.execute()` yourself, inside the revision |

Two further rules from the migration subsystem's own README:

- **`downgrade()` must work.** A migration you cannot roll back is one you
  cannot deploy safely — which is exactly what `downgrade -1` in the workflow
  above is checking.
- **One revision per logical change**, and an applied revision is never edited.
  Changing a model means adding a revision, not editing the one that created the
  table.

**`sqlalchemy.url` is empty on purpose.** `backend/alembic.ini` sets it to an
empty string, and the file's own header explains why: credentials must never
live in a git-tracked file. `migrations/env.py` resolves the URL through
`get_url()`, which falls back to `app.core.config.get_settings().sqlalchemy_database_uri`.
To migrate a different database, override the environment, not the ini file:

```bash
DATABASE_URL=postgresql+psycopg://nexus:nexus@127.0.0.1:5432/nexus_test \
  python -m alembic upgrade head
```

### 4.3 Naming and the chain

| Property | Value | Where |
| --- | --- | --- |
| File name | `<rev>_<slug>` (`file_template = %%(rev)s_%%(slug)s`) | `alembic.ini` |
| Slug length | 40 characters, trimmed | `alembic.ini` (`truncate_slug_length`) |
| Timestamps in filenames | off — generated names stay diff-friendly | `alembic.ini` (`revision_environment = false`) |
| Scope | schema `public` only; `alembic_version` and `spatial_ref_sys` excluded | `migrations/env.py` (`include_object`) |
| UUID rendering | `postgresql.UUID(as_uuid=True)`, dialect stated explicitly | `migrations/env.py` (`render_item`) |
| Comparison | `compare_type` and `compare_server_default` both on | `migrations/env.py` (`_autogenerate_options`) |

The chain has exactly one head and is asserted to be linear by
`backend/tests/test_migrations.py::test_the_migration_chain_is_linear_and_has_a_single_head`.

> **Known constraint.** That test asserts the full revision list literally —
> `== ["0010", "0009", "0008", "0007", "0006", "0005", "0004", "0003", "0002", "0001"]`,
> head `0010` — so adding a revision makes it fail until the list is extended. That is a
> deliberate pin on the current chain, not an oversight. Phase 2 extended it when it added
> `0002`, Phases 3 through 9 extended it again, and the remediation wave added `0010`.

Drift is also a test, not just a command:
`test_autogenerate_reports_no_drift` compares the live schema against
`Base.metadata` with the same options `env.py` uses. It is `integration`-marked, so it
needs a reachable PostgreSQL — and it now runs, because a native PostgreSQL 16 is
available in the development environment and `alembic upgrade head` is applied by the
`test_database_url` fixture.

`backend/tests/test_migration_ddl.py` is the database-free substitute, and it is
worth reading before you add a revision. It renders the whole chain to SQL
**offline** (`as_sql=True` into a buffer) and compares every emitted `CREATE
TABLE` column, foreign key and index against `Base.metadata`. Adding a revision
without adding it to `MIGRATION_MODULES` in that file means the offline check
silently stops covering the new head.

---

## 5. Adding a frontend page

Adding a module page is a **three-file change**. All three are required, and each
one missing produces a different loud failure rather than a blank page — which is
why the pattern is worth following exactly.

### 5.1 The three files

| # | File | What you add | Reference |
| --- | --- | --- | --- |
| 1 | `frontend/src/features/modules/catalog.ts` | a `ModuleDefinition` entry in `MODULES` (`to`, `label`, `summary`, `vision`, `phase`, `icon`, `capabilities`, `metrics`, `keywords`) and the matching `getModule('/<path>')` in one of the `NAV_GROUPS` | `MODULES` and `NAV_GROUPS` |
| 2 | `frontend/src/routes/lazy-pages.ts` | `export const <Name>Page = lazy(() => import('@/pages/<name>-page'))` | the whole file is this pattern |
| 3 | `frontend/src/routes/router.tsx` | a route object `{ path: '/<path>', element: <NewModulePage /> }` in the `AppLayout` children, plus the name in the import block | the `createBrowserRouter` table |

A page that is not a module placeholder needs no catalog entry; add it to the
route table alone. But if it appears in the sidebar or the command palette, it
needs all three.

### 5.2 The registry cannot drift from the route table

`getModule(path)` in `catalog.ts` **throws on an unknown path**. That is
deliberate and it is what makes the three-file pattern load-bearing:

```ts
export function getModule(path: string): ModuleDefinition {
  const match = MODULES.find((module) => module.to === path)
  if (!match) throw new Error(`Unknown module path: ${path}`)
  return match
}
```

| Failure | What happens |
| --- | --- |
| Route added, catalog entry missing | `getModule()` throws at render; the sidebar, palette and header all call it |
| Catalog entry added, route missing | `NAV_GROUPS` renders a link to a path the router resolves to `*` → `NotFoundPage` |
| Both present, `to` values disagree by a character | the first case again — the registry is the authority on paths |

`NAV_GROUPS` calls `getModule()` at module scope, so a malformed `to` in a group
fails at import time rather than on click.

### 5.3 The page file itself

The whole of a placeholder page:

```tsx
// frontend/src/pages/projects-page.tsx
import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function ProjectsPage() {
  return <ModulePage module={getModule('/projects')} />
}
```

Three conventions:

1. **Pages use a default export.** `lazy(() => import(...))` needs one.
   Primitives in `components/ui/` use named exports.
2. **The page passes `children` for anything module-specific**, appended below
   the standard body — `ModulePage` accepts `ModulePageProps.children`. Do not
   fork the placeholder layout to add one card.
3. **Copy comes from the registry.** `summary`, `vision`, `capabilities` and
   `metrics` are real, module-specific sentences written for that module. A page
   that invents its own header copy has forked the registry for no reason.

### 5.4 When the page becomes live

A placeholder page that starts fetching needs, in addition:

| Layer | File | Rule |
| --- | --- | --- |
| Wire types | `frontend/src/types/api.ts` | mirror the Pydantic schemas, same `snake_case` spelling |
| Transport | `frontend/src/services/<module>.ts` | one exported function per endpoint; no `fetch` outside `lib/api-client.ts` |
| Server state | a hook in `frontend/src/features/<module>/` | TanStack Query; the query client suppresses retries for 4xx |
| Error handling | `ApiError` | branch on `code`, never on `message` |

### 5.5 A settings panel

`/settings` is the one page in the product that is a *composition* rather than a
module: a five-tab surface (`Profile`, `Account`, `Security`, `Sessions`,
`Preferences`) with one panel per tab in `frontend/src/features/settings/`. A new
account-level capability belongs there rather than in a new page.

The pattern, from `sessions-panel.tsx`:

| Layer | File | Rule |
| --- | --- | --- |
| Transport | `frontend/src/services/sessions.ts` | One exported function per endpoint. `listSessions`, `revokeSession`, `logoutAll` — and nothing calls `fetch` outside `lib/api-client.ts` |
| Query | `useQuery` / `useMutation` in the panel | Invalidate on success rather than refetching by hand; `onSessionChange` is what the query layer subscribes to so cached account data cannot outlive the session that fetched it |
| Presentation | the panel file | No `ApiError` strings hard-coded in JSX — branch on `code`, and route a 404 in the session-revoke path to "already gone" rather than to an error banner, because that is what a double-click produces |
| Destructive actions | `dialog.tsx` + `danger-zone.tsx` | A confirm dialog for anything irreversible, and a red-bordered region that says so in words |
| Success feedback | `useToast()` | A mutation that succeeds says so. Silent success is indistinguishable from a dropped click |

Two rules the account work established, which a new panel must not break:

- **An action that ends other sessions must be visible next to the session list.**
  A password change revokes every session except the caller's, so `Security` and
  `Sessions` are separate tabs but the consequence is stated in the copy of both.
  Do not hide a cross-panel side effect.
- **A destructive request carries its own proof.** `DELETE /users/me` requires the
  account password *and* `confirm: true` in the body, because a token left in a
  shared browser is enough to read an account but not enough to destroy one. A new
  irreversible endpoint should follow that pattern rather than rely on the dialog.

### 5.6 No fabricated data

**A module page renders real copy, not invented numbers.** Every metric tile on
a placeholder renders an em dash and states what it will need:

```tsx
<p className="…text-muted-foreground/50">—</p>
<p className="…text-muted-foreground">{hint}</p>   // e.g. "Requires project records"
```

and the panel beside it says *"Nothing is stored here until Phase N."* This is a
standing rule, not a placeholder to be cleaned up later. A screenshot with a
plausible-looking `47` in it is a lie the code cannot retract, and the next
person to read the page has no way to tell it from a real measurement. If a
number is not backed by a query, it is an em dash.

---

## 6. Testing conventions

### 6.1 The markers

Two are registered in `backend/pytest.ini`:

```ini
markers =
    integration: requires a live PostgreSQL instance
    ml_model: requires the trained Phase 10 checkpoint on disk and torch installed;
              skipped when either is absent
```

`--strict-markers` is on, so an unregistered marker is an error rather than a
no-op.

| Rule | Detail |
| --- | --- |
| What to mark | Any test that touches the database — which in practice means any test whose signature pulls in `client`, `db_session` or `truncated_database` |
| How | Module-level `pytestmark = pytest.mark.integration`, as in `test_auth.py`, `test_repositories.py`, `test_errors.py`, `test_migrations.py`; per-test `@pytest.mark.integration` where only one test needs it |
| What must pass offline | `python -m pytest -m "not integration"` with PostgreSQL stopped |
| Current split | 2270 collected — 1029 offline, 1241 `integration`. The integration half needs a native PostgreSQL 16 |

Mark a test `integration` because it genuinely needs a database — not because it
is easier to get green that way. The offline subset is the fast inner loop; a
contributor who cannot run it cannot iterate.

#### The `ml_model` marker, and why it had to be added

Phase 11 is the **first suite in this repository that a clean clone cannot run**.
`backend/ml/artifacts/` is gitignored — a 703 MiB checkpoint is a training output, not
something the repository ships — and `torch` is a large, separate install. The Phase 10
suite was deliberately 100% offline and torch-free, so without a marker these tests would
have been the first in the project to hard-fail on a fresh checkout, on a machine that had
done nothing wrong.

The marker follows the same philosophy as `integration`: **name the environmental
precondition, and skip cleanly with a reason when it does not hold.** A test that silently
stops covering the classifier is worse than one that says why it could not run.

| Rule | Detail |
| --- | --- |
| How to run it | `python -m pytest tests/test_ml_integration_*.py` — five files, **630 passed, 14 xfailed** on the machine that has the checkpoint |
| What it skips on | No checkpoint at `backend/ml/artifacts/small-model/final/`, or no `torch`. The skip message names which of the two |
| The `xfail`s | Measured generalisation failures, pinned on purpose — see [§6.7](#67-the-phase-11-suite-and-its-fourteen-xfails) |
| What still runs without either | Everything else. `import app.main`, `import app.ml` and `import app.api.deps` never import torch, so the rest of the suite is unaffected |

### 6.2 Choosing a client fixture

The three HTTP fixtures differ in two ways, and both matter:

| Fixture | Transport | Database | Use for |
| --- | --- | --- | --- |
| `offline_client` | `ASGITransport(app)` | none | Anything that must pass with PostgreSQL down: `/health`, error-envelope shapes, logging, middleware, config-driven routes |
| `client` | `ASGITransport(app)` | `engine` + `truncated_database` | Routes that read or write data |
| `non_raising_client` | `ASGITransport(app, raise_app_exceptions=False)` | none (add `truncated_database` if needed) | Tests that deliberately make the app raise |

`ASGITransport` defaults to `raise_app_exceptions=True`, which re-raises inside
the test — so the rendered 500 the user would have received is never observable,
and the `internal_error` catch-all handler cannot be asserted on at all. That is
the only reason `non_raising_client` exists.

None of them runs the application lifespan. Do not write a test that assumes a
startup or shutdown hook fired; the `engine` fixture installs the test database
behind `app.db.session` instead, which is what the lifespan would have done.

### 6.3 Database fixtures

| Fixture | Scope | Behaviour |
| --- | --- | --- |
| `test_database_url` | session | Creates `nexus_test` if missing (via an AUTOCOMMIT connection to the `postgres` maintenance database), then `alembic upgrade head` |
| `engine` | session | `NullPool` engine on the test database, installed as the application's engine for the whole session, restored on teardown |
| `truncated_database` | function | `TRUNCATE … RESTART IDENTITY CASCADE` over every table in `Base.metadata.sorted_tables` |
| `db_session` | function | An `AsyncSession` on that database |
| `settings` | function | Clears the `get_settings` `lru_cache` around the test so a monkeypatched variable cannot leak |
| `make_settings` | function | Builds a `Settings` from explicit `monkeypatch.setenv` overrides |

Three rules follow from how these are built:

- **`Base.metadata.create_all` is never used.** The schema under test comes from
  the migrations, or it proves nothing about the migration.
- **`NullPool` is required, not an optimisation.** `pytest.ini` scopes the asyncio
  loop to a single test, so a pooled connection opened under one loop would be
  reused under the next.
- **Repositories commit.** A `db_session` fixture's `rollback()` does not undo a
  committed write — that is what `truncated_database` is for.

### 6.4 Asserting on an error

`assert_error_envelope` asserts the whole contract, not just the status:

```python
error = assert_error_envelope(response, status_code=409, code="conflict")
assert error["request_id"] == response.headers["X-Request-ID"]
```

It checks the status, that the top-level payload is exactly `{"error": …}`, that
the error object has exactly `code` / `message` / `details` / `request_id`, that
the message is a non-empty string, and that the body's `request_id` equals the
response header. Use it for **every** failure path, not the interesting one.

`assert_no_internals` is a plain function in `tests/test_errors.py`, not a fixture —
import it (`from tests.test_errors import assert_no_internals`) when you need it.
It fails if the body contains any fragment from `FORBIDDEN_FRAGMENTS`: a
traceback, a driver or ORM name (`psycopg`, `sqlalchemy`, `asyncpg`, `alembic`),
a SQL keyword, a file-path fragment, a column name like `users.email`, or an
internal package path. Pair it with `assert_error_envelope`.

### 6.5 Frontend tests

| Property | Value |
| --- | --- |
| Runner | Vitest, `jsdom`, `globals: false` — import `describe`/`it`/`expect` from `vitest` |
| Config | `test` block in `frontend/vite.config.ts`; `include: ['src/**/*.{test,spec}.{ts,tsx}']` |
| Setup | `src/test/setup.ts`, applied per test: `@testing-library/jest-dom/vitest`, `cleanup()`, and shims for `scrollIntoView`, `matchMedia` and `AbortSignal` |
| Location | Colocated next to the subject (`components/ui/button.test.tsx`), not in a `__tests__` folder |
| Current suite | 44 files, 645 tests (`npm test` from `frontend/`, run during the final remediation pass) |

Individual test files should not add their own environment shims — the setup
file installs them in `beforeEach` and `unstubAllGlobals` in `afterEach` would
otherwise tear them down. Stub what a test needs through `vi.stubGlobal` /
`vi.spyOn`; the teardown handles the rest.

### 6.6 The regression rule

**A bug fix lands with a test that fails when the fix is reverted.**

That is the whole criterion: revert the one line that fixed it, run the new test,
watch it go red, put the line back, watch it go green. If it passes both ways it
is not testing the fix. The same applies to a feature guard — a test that cannot
fail is a comment that costs a test run on every future change.

In practice that means a new test that:

- asserts the specific behaviour that was broken, not the general happy path;
- exercises the real code path — the real `offline_client`/`client`, the real
  component render — rather than a mock of it;
- and, for an envelope failure path, uses `assert_error_envelope` so it also
  guards the shape.

### 6.7 The Phase 11 suite and its fourteen xfails

`tests/test_ml_integration_*.py` is five files and it is structured by layer, not by
feature, because that is the order in which a failure means something:

| File | What it is the only place that proves |
| --- | --- |
| `test_ml_integration_loading.py` | Checkpoint resolution, the label cross-validation, device selection, the failure modes, and that importing the app leaves `sys.modules` free of torch |
| `test_ml_integration_classification.py` | All fourteen intents end to end, confidence integrity, truncation at 128 tokens, and the validation rules |
| `test_ml_integration_routing.py` | The routing policy, the threshold table, and the guard that stops a second model being introduced |
| `test_ml_integration_api.py` | The HTTP surface: auth, permissions, the error envelope, and a live end-to-end request |
| `test_ml_integration_config.py` | The seven `ML_*` settings, the lifespan, single-load, concurrency, and that the submitted text never reaches a log |

**The fourteen `xfail`s are not skipped coverage.** They are measured generalisation
failures, pinned with `pytest.mark.xfail(strict=…)` so they cannot quietly disappear — and
so that a *fix* to one of them turns the suite red until the pin is removed deliberately,
which is the point: a measurement that cannot fail has stopped being one.

Two sets of utterances were measured, and they disagree:

| Set | What it is | Result |
| --- | --- | --- |
| A — 34 held-out representative phrasings | Hand-written for this phase, all fourteen intents, **not** copied from the training corpus | **34/34 correct** |
| B — 56 natural-language phrasings | Written specifically to break the model: lower case, ALL CAPS, no question mark, terse mobile phrasing, first person, vocabulary away from the domain nouns the synthetic corpus leans on | **42/56 — 75.0%** |

The second figure is the one to remember and the one not to soften. Two findings sit
behind it, and both belong in the mind of anyone who writes UI over this endpoint:

- **Seven of the fourteen misses collapse into `risk_query`.** The `out_of_scope` class was
  meant to absorb "nothing here fits", but the model treats `risk_query` as the sink for
  anything conversational, uncertain or reflective it cannot place. It is a wrong *read*
  rather than a wrong write — `RiskDetectionService.evaluate` does not mutate — but it is a
  wrong answer delivered confidently.
- **The confidence threshold does not catch them.** Seven of the fourteen were predicted at
  0.82 or above and five at 0.90 or above — at or past the shipped threshold of 0.90.

So low-confidence routing to `uncertain` is a real safety property and it is **not** a
general accuracy defence. Branch on `status`, show `reason` and `alternatives`, and never
treat `accepted` as a command to issue. The full table of the fourteen misses, with the
confidence each one drew, is in
[`specifications/phase-11-report.md`](specifications/phase-11-report.md).

The Phase 10 regression suite (`tests/test_ml_*.py`, thirteen files, **321 passed**) is
unchanged by all of this and still runs with no checkpoint and no torch.

---

## 7. Design-system rules

### 7.1 Tokens come from `index.css`, not from a class

`frontend/src/index.css` defines **bare HSL channels** — `212 85% 50%`, not
`hsl(212 85% 50%)` — in both `:root` and `.dark`. `frontend/tailwind.config.ts`
maps them with `<alpha-value>`:

```ts
primary: {
  DEFAULT: 'hsl(var(--primary) / <alpha-value>)',
  foreground: 'hsl(var(--primary-foreground) / <alpha-value>)',
},
```

That is what makes `bg-primary/10` work at all. **The consequence for you:** to
add a colour, add the channel triplet to `:root` *and* `.dark` in `index.css`,
then add the mapping in `tailwind.config.ts`. Do not write `bg-[hsl(212_85%_50%)]`
in a component — it will not respond to the theme.

Radius and typography are tokens too (`--radius` drives the `lg`/`md`/`sm`
`borderRadius` scale; `fontFamily.sans` is Inter with system fallbacks).

### 7.2 Primitives are shadcn-style, over Radix

`frontend/src/components/ui/` holds the primitives. They follow the shadcn/ui
convention (configured in `frontend/components.json`, new-york style, lucide
icons): the component source lives in this repository rather than in
`node_modules`, so a change to a primitive is a normal edit.

**But only seven of them are over Radix.** `frontend/package.json` installs
exactly these: `@radix-ui/react-avatar`, `-dropdown-menu`, `-label`,
`-scroll-area`, `-separator`, `-slot`, `-tooltip`. Phase 2 needed six more
primitives and the corresponding packages were not installed, so it wrote them by
hand:

| Primitive | Over Radix? | What it has to get right |
| --- | --- | --- |
| `dialog.tsx` | **no** | Focus trap, focus restore to the trigger on close, `Escape`, overlay dismissal, scroll lock, `aria-modal`, `role="dialog"` + `aria-labelledby` |
| `tabs.tsx` | **no** | Full ARIA: `tablist` / `tab` / `tabpanel`, `aria-selected`, `aria-controls`, `aria-labelledby`, arrow-key navigation and a **roving tabindex** (exactly one tab is `tabIndex=0`; the rest `-1`) |
| `progress.tsx` | **no** | `role="progressbar"` with `aria-valuenow` / `aria-valuemin` / `aria-valuemax` / `aria-valuetext` |
| `alert.tsx` | **no** | `role="alert"` for an urgent message, `role="status"` for a polite one |
| `switch.tsx` | **no** | `role="switch"` + `aria-checked` on a `<button>`; Space and Enter both toggle |
| `select.tsx` | **no** | **A native `<select>`, deliberately** — see below |
| `toast.tsx` / `toaster.tsx` | **no** | A Zustand store plus an ARIA live region; `role="status"` for polite, `role="alert"` for destructive |

`select.tsx` is the one that looks like an oversight and is not. A hand-built
listbox is where accessibility goes to die: type-ahead and its buffer, `Home` /
`End`, arrow wrap-around, screen-reader announcements of the active option and of
how many options exist, and the platform picker on touch are all hard to get
right, and the native element gets every one of them from the OS. The price is
that it cannot be styled like the rest of the system on every platform — the
correct trade for a control that picks a colour scheme, and the reason it is
documented here rather than left as an apparent inconsistency.

`avatar`, `dropdown-menu`, `label`, `scroll-area`, `separator`, `slot` and
`tooltip` **are** Radix, and should stay that way.

The pattern, from `button.tsx`:

```tsx
const buttonVariants = cva('…base classes…', {
  variants: {
    variant: { default: '…', secondary: '…', outline: '…', ghost: '…', destructive: '…' },
    size:    { sm: '…', default: '…', lg: '…', icon: '…' },
  },
  defaultVariants: { variant: 'default', size: 'default' },
})

export interface ButtonProps
  extends React.ComponentPropsWithoutRef<'button'>,
    VariantProps<typeof buttonVariants> { … }
```

Rules that follow from it:

| Rule | Why |
| --- | --- |
| Styling variation is a `cva` variant, not a conditional `className` at the call site | One place decides what "secondary" means; a caller cannot half-apply it |
| New variants are added to the map, not invented per use | A page that needs a sixth variant gets a sixth variant |
| Component-level overrides go through `className`, merged with `cn` | `cn` is `twMerge(clsx(...))` (`lib/utils.ts`) — later utilities win, so `className="w-full"` really does override a `size` width |
| `asChild` renders the child with the styles, via Radix `Slot` | `<Button asChild><Link/></Button>` is the correct composition; nesting a button in an anchor is not |
| Forward refs; set `displayName` | Radix and the React DevTools both depend on them |

### 7.3 Writing a new design-system primitive

If the primitive is not over Radix, it is yours to maintain — so it owes the same
things a Radix package would have given you:

| Obligation | Detail |
| --- | --- |
| Full ARIA, not decorative | Every interactive primitive needs its role, its state attributes, and the label/control relationships a screen reader needs. `tabs.tsx` and `switch.tsx` are the models |
| Keyboard parity with a native control | Arrows, `Home`/`End`, `Space` and `Enter` — whatever the platform equivalent would do. A primitive that only responds to a mouse is a bug |
| Focus management | Roving tabindex for a set, focus trap and restore for an overlay, visible focus from the global `:focus-visible` rule |
| An escape hatch | `className` merged through `cn`, so a caller can adjust layout without a new variant |
| A colocated test | `button.test.tsx`, `card.test.tsx`, `dialog.test.tsx` and `tabs.test.tsx` are all colocated in `components/ui/`. A hand-rolled primitive with real behaviour and no test is the one thing this repository does not ship |

**Prefer not to build one.** The list above is the cost, and it is why
`select.tsx` is a native element: the cost of not having Radix should be paid
where the platform already did the work.

### 7.4 Structural helpers, and what not to build by hand

`index.css` also owns a small `@layer components` block:

| Helper | Use |
| --- | --- |
| `.app-container` | Page gutter and max width (`1400px`, responsive padding). The dashboard, settings, not-found and module pages all wrap their outermost element in it; the login and register pages use `AuthShell` instead |
| `.app-scroll` | The scroll region inside the app shell, so the page does not scroll twice |

Beyond that, prefer the existing building blocks over a new one:

| Need | Use |
| --- | --- |
| Empty or waiting state | `components/feedback/empty-state.tsx` |
| Loading | `components/feedback/loading-state.tsx` (framed region) or `components/ui/skeleton.tsx` (inline placeholder) |
| Error | `components/feedback/error-state.tsx` — retryable, and it reads `ApiError` |
| Unhandled render error | `components/feedback/app-error-boundary.tsx`, wired as the router's `errorElement` |
| Page title block | `components/feedback/page-header.tsx` — takes `eyebrow`, `title`, `description`, `badges` |
| A modal, menu, tooltip, separator, avatar | The matching `components/ui/` primitive |
| Confirmation before something irreversible | `components/ui/dialog.tsx`, plus the `danger-zone.tsx` pattern in `features/settings/` |
| Tabbed settings or a tabbed sub-view | `components/ui/tabs.tsx` — full ARIA and roving tabindex already implemented |
| A transient confirmation, or a non-blocking error | `useToast()` from the toast store; do not use a dialog for something the user did not have to answer |
| A password field with a reveal toggle and a live checklist | `features/auth/components/password-field.tsx` and `password-rules-checklist.tsx` |
| A binary setting | `components/ui/switch.tsx`; `input.tsx` for text |

Accessibility is not optional styling. `:focus-visible` is defined globally in
`index.css` so focus is visible on native *and* Radix elements; `prefers-reduced-motion`
collapses durations rather than removing transitions. Decorative icons carry
`aria-hidden="true"` (`module-page.tsx` does this on every icon it renders) so
they are not announced. The hand-rolled primitives carry their own ARIA
([§7.3](#73-writing-a-new-design-system-primitive)) — if you extend one, extend the
roles with it.

---

## 8. Code conventions

### 8.1 Backend — ruff

Configured in `backend/pyproject.toml`. Both `check` and `format --check` must be
clean; `migrations/` is excluded because Alembic boilerplate is not ours to lint.

| Setting | Value |
| --- | --- |
| Line length | 100 (`[tool.ruff] line-length`) — and `E501` is *ignored*, because the formatter owns line length |
| Target | `py313` |
| Import sorting | isort (`I`), `known-first-party = ["app"]` |
| Docstrings | pydocstyle (`D`), **Google convention** |
| Also enabled | pycodestyle, pyflakes, pep8-naming, pyupgrade, bugbear, builtin-shadowing, comprehensions, simplify, ruff-specific, async, **bandit (`S`)** |

Bandit is on, so findings like a hardcoded credential, an `assert` outside
`tests/`, or an unchecked subprocess call are lint errors in `app/`. `S105` is
ignored because a settings default may legitimately look like a hardcoded secret.

### 8.2 Docstrings explain why

The convention is enforced by the *quality* rules (`D2xx`, `D4xx`) while the
*presence* rules are relaxed: `D105` (magic methods) and `D107` (`__init__`) are
ignored, and `tests/*` is exempted from docstring requirements. The rationale is
written into `pyproject.toml`: demanding a docstring on every dunder or Pydantic
validator produces noise, not information.

So: module docstrings and public functions get one, and they say something a
reader could not infer from the code.

| Weak | Strong |
| --- | --- |
| `"""Register a user."""` | `"""Create an account, rejecting an address that is already taken."""` |
| `"""Caches the hash."""` | `"""Return a bcrypt hash of a random value, computed once per process. …so that the missing-account path pays the same bcrypt cost as a real check…"""` — `_decoy_hash`, `auth_service.py` |

The module docstring of `auth_service.py` is the model: it says what the module
owns *and* what it refuses to do (import FastAPI), and that is why the file can
be read as a rule rather than a list of functions.

Inline comments follow the same rule — `backend/app/repositories/user.py`
explains why `create()` calls `refresh()`, not that it does.

### 8.3 Backend — imports and typing

| Convention | Detail |
| --- | --- |
| First-party | `app` is the only first-party name; import from the package, not by path |
| Forward references | `from __future__ import annotations` at the top of every non-empty module in `app/`, `tests/` and `migrations/` (the only exceptions are the empty package `__init__.py` files) |
| Async | `async def` all the way down through router → service → repository; nothing blocks the loop |
| Timezone-aware datetimes | `datetime.now(UTC)`, never naive `utcnow()` |
| Errors | raise a `NexusError` subclass from `app.core.exceptions`, with a `raise … from exc` when wrapping |
| Line length | 100 columns, enforced by the formatter — run it, do not hand-wrap |

### 8.4 Frontend — TypeScript

`frontend/tsconfig.app.json` is strict in the ways that catch real bugs:

| Option | Consequence for you |
| --- | --- |
| `strict` | No implicit `any`, no implicit `undefined` from a possibly-absent value |
| `noUncheckedIndexedAccess` | `arr[0]` is `T \| undefined`. Narrow it — a bare `arr[0].name` will not compile |
| `verbatimModuleSyntax` | Type-only imports must say `import type { … }`. `import { type Foo }` is the wrong form |
| `noUnusedLocals`, `noUnusedParameters` | An unused import is an error, not a hint |
| `noEmit` | Vite owns emit; `tsc -b` only type-checks |
| `paths` | `@/*` maps to `src/*` — always import through it, never by relative path out of a subtree |

Runtime types come from `src/types/api.ts` and mirror the Pydantic schemas in
`snake_case`, with no aliases and no camelCase bridge.

### 8.5 The `react-refresh` constraint

`frontend/eslint.config.js` turns on `react-refresh/only-export-components` as an
**error**. It exists so Vite's fast refresh can swap a component without
re-executing the module — and it will bite you the first time you export a helper
next to a component:

```
Fast refresh only works when a file only exports components.
```

**Where helpers must live, then:**

| Want | Do this |
| --- | --- |
| A non-component helper used by a component | Move it to its own module — `features/`, `lib/`, or a sibling file — and import it |
| A `cva` variant map | Export it from the primitive file, but add its name to `allowExportNames` in `eslint.config.js` — `buttonVariants`, `badgeVariants` and `spinnerVariants` are already there |
| A constant next to a component | Allowed: `allowConstantExport: true` covers exported constants |

The whitelist is an explicit, reviewed list, not a pattern. A new variant map
requires editing `eslint.config.js` in the same commit — which is the intended
friction: it makes the decision visible.

### 8.6 Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Python modules, functions, variables | `snake_case` | `auth_service.py`, `get_authenticated_user` |
| Python classes | `PascalCase` | `AuthService`, `RevocationStore` |
| React components, types | `PascalCase` | `ModulePage`, `ModuleDefinition` |
| Files and directories | `kebab-case` | `module-page.tsx`, `api-client.ts`, `use-health.ts` |
| Exported types | `PascalCase`, usually the component name plus `Props` or the domain noun | `ButtonProps`, `ModuleCapability` |
| Hooks | `use-` prefix, file named after the hook | `use-health.ts` |

---

## 9. Before you open a pull request

| # | Check | Command | Where |
| --- | --- | --- | --- |
| 1 | Backend lint | `python -m ruff check .` | `backend/` |
| 2 | Backend format | `python -m ruff format --check .` | `backend/` |
| 3 | Frontend lint | `npm run lint` | `frontend/` |
| 4 | Frontend types | `npm run typecheck` | `frontend/` |
| 5 | Backend tests | `python -m pytest -m "not integration"` | `backend/` |
| 6 | Backend tests, full | `python -m pytest` — only if PostgreSQL is running | `backend/` |
| 6a | ML tests | `python -m pytest tests/test_ml_integration_*.py` — only if the checkpoint and torch are present; otherwise it skips and says why | `backend/` |
| 7 | Frontend tests | `npm test` | `frontend/` |
| 8 | Migration drift | `python -m alembic check` — **required if you touched a model** | `backend/` |
| 9 | Build | `npm run build` — required if you touched routing, imports or a chunk rule | `frontend/` |

`make lint` covers 1–4 and `make test` covers 5–7. Row 6a is manual on purpose: it needs a
703 MiB checkpoint a fresh checkout does not have, so making it part of `make test` would
mean the target skips on a clean tree.

And, not command-shaped:

- [ ] Every failure path you added is asserted with `assert_error_envelope`.
- [ ] Every bug fix landed with a test that fails when the fix is reverted
      ([§6.6](#66-the-regression-rule)).
- [ ] Nothing fabricated: no placeholder renders a number it cannot source
      ([§5.6](#56-no-fabricated-data)).
- [ ] The wire contract still holds — errors raised from the service, not the
      router; schemas own their constraints.
- [ ] A new endpoint touching a caller-scoped row scopes its lookup by the caller
      and answers **404**, not 403 ([§3.3](#33-a-permission-guarded-endpoint)).
- [ ] A new protected route declares a `Permission`, and that permission is
      actually granted in `ROLE_PERMISSIONS` — an unlisted one denies admins too.
- [ ] Nothing written to `audit_logs.metadata` is a password, a token, or a hash
      of either ([§3.8](#38-recording-an-audit-event)).
- [ ] New lines are below the DEV MARKER side of `backend/requirements.txt`, or
      above it and genuinely needed at runtime. The Dockerfile strips everything
      below the marker.
- [ ] `scripts/verify_compose.py` still passes if you touched
      `docker-compose.yml` or `.env.example`.
- [ ] Any new `Settings` field is documented in **both** `.env.example` and the
      README's environment-variable table, with its default. `backend/tests/test_documentation_claims.py`
      fails the build if one is missed — fifteen settings went undocumented through
      nine phases before that test existed.
- [ ] If you touched `app/ml/`, the two ML endpoints or their schemas,
      `pytest tests/test_ml_integration_*.py` was run — or the skip reason is stated
      in the change. "It skipped" is an answer; "it was skipped and nobody said why"
      is not.
- [ ] Any number you changed in a document was **measured**, not estimated, and the
      measurement command is named next to it.

---

## 10. Verified baseline and known limits

These are the numbers this document was written against. They are results, not
projections: every row was produced by running the command in the environment
described immediately below the table. The first three rows were captured during the
final remediation pass over Phases 1–9 and therefore **predate Phases 10 and 11**; the
Phase 11 rows were run afterwards, on the same machine, and both sets are labelled with
which is which.

| Command | Working directory | Result |
| --- | --- | --- |
| `python -m pytest --collect-only -q` | `backend/` | **2270 collected** — the full suite, as of the Phases 1–9 remediation pass |
| `python -m pytest --collect-only -q -m "not integration"` | `backend/` | **1029 collected**, 1241 deselected |
| `python -m pytest --collect-only -q -m integration` | `backend/` | **1241 collected**, 1029 deselected |
| `python -m pytest` | `backend/` | **3,221 passed, 14 xfailed, 1 skipped** — after Phase 11. The skip is the pre-existing Windows symlink-privilege skip in `test_developer_git.py`; the xfails are the measured generalisation failures of §6.7 |
| `python -m pytest tests/test_ml_integration_*.py` | `backend/` | **630 passed, 14 xfailed** — needs the trained checkpoint |
| `python -m pytest tests/test_ml_*.py` | `backend/` | **321 passed** — the Phase 10 ML regression suite, unchanged by Phase 11 |
| `ruff check .` | `backend/` | clean |
| `ruff format --check .` | `backend/` | 189 files already formatted, none to rewrite |
| `npm test` | `frontend/` | 44 files, **645 tests** passing |
| `python scripts/verify_compose.py` | repository root | passes — 3 services, every Compose variable documented in `.env.example` |

**The first three rows are collection counts, and the difference matters.**
`pytest --collect-only` proves what the suite *contains*; it does not prove the
suite *passes*. They are quoted unchanged because they are the figures the
documentation set pins, and they now understate a suite that has grown by two
phases — the full **run** above is the current answer to "does it pass", and it is a
pass count. The frontend row is a real pass count too: `npm test` touches no database
and does not contend for the `nexus_test` advisory lock, so it was run end to end.

`ruff format --check .` reported 22 files to rewrite when this section was first
written — 10 under `app/` and 12 under `tests/`, every one of them a Phase 8 or
Phase 9 addition. That is closed: the formatter now reports 189 files already
formatted and none to rewrite. Both tools are clean, so this row is green
rather than "recorded and explained".

Uncompressed `frontend/dist/assets/` chunk sizes from that build:

| Chunk | Bytes |
| --- | --- |
| `charts` | 432,148 |
| `react` | 222,425 |
| `radix` | 113,444 |
| `index` (entry) | 104,155 |
| `router` | 92,238 |
| `learning-page` (largest route) | 67,842 |
| `career-page` | 52,538 |
| `icons` | 46,668 |
| `data` | 37,965 |
| `developer-page` | 35,303 |
| `knowledge-page` | 34,610 |
| `planner-page` | 32,662 |
| `settings-page` | 31,313 |
| `module-page` (the three placeholders) | 2,754 |

Two things to read off that table. `charts` is now the largest chunk in the bundle,
because Analytics shipped and pulls in recharts — it was reserved for a module that did
not exist when this list was first written. And the three remaining placeholder routes
share one 2,754 B `module-page` chunk, because the code lives in `ModulePage` and the
registry; a placeholder chunk growing by kilobytes means something page-specific has
crept in.

### What was **not** verified here

The environment this baseline was captured in has a working **native PostgreSQL 16** but
**no Docker** — `docker` and `docker compose` are both absent from it. That means:

| Not run | Why it matters |
| --- | --- |
| `docker compose up` | `docker-compose.yml` has never been executed by `docker compose`. `scripts/verify_compose.py` validates it statically — Compose v2 syntax, three services, real build contexts, existing bind mounts, every `${VAR}` documented — and cannot tell you the stack starts. Treat the first run as untested, and note that the images themselves have never been built either |
| The containers in the production configuration | Nothing here says the `backend` or `frontend` Dockerfile works; only that the repository they build from lints, type-checks, tests and builds. `backend/Dockerfile:29` also installs the runtime requirements **without** `--extra-index-url`, so the image build currently fails on the `torch==2.14.1+cpu` pin for the reason in [§1.2](#12-bootstrap) |
| `postgresql:16-alpine` | The suite runs against native PostgreSQL 16.2 on Windows. The Compose path uses the Alpine image and its `docker/postgres/init/` extension script, which nothing here has executed |
| Any GPU | Every Phase 11 figure — load time, cold and warm inference, the long-input case — comes from a **CPU-only** machine with 14 torch threads. `ML_DEVICE=cuda` is implemented and fails loudly when CUDA is absent, but no CUDA latency was measured and none is estimated anywhere in this documentation set |

Everything database-backed **was** run, and against the real thing: the 1241
`integration` tests cover the repositories, sessions, account deletion, RBAC, password
reset, the drift check and the detailed-health endpoint, and `alembic upgrade head` is
applied by the `test_database_url` fixture on every integration session. So the migration
chain and the queries built on it are exercised, not merely rendered offline.

Three further limitations that are properties of the code, not of the environment, so
that none is mistaken for a bug to route around:

- **`audit_log_retention_days` has no enforcing job.** The setting states a policy
  and gives the value somewhere to be displayed; nothing prunes `audit_logs`, so the
  table grows for as long as the install lives.
- **`GET /api/v1/users/` is a permission-system fixture, not a product feature.**
  It exists so the role → permission wiring has a route whose refusal is observable
  end to end, and it returns an unbounded list because a fixture that could itself
  need pagination would be a worse fixture. Do not build UI on it.
- **Search, AI Assistant and Experiments have no backend at all.** They are the three
  pages still rendering `ModulePage`, and `/search` in particular is a shortcut hint
  pointing at work that has not been done.

---

## 11. Developer, Learning, Career and ML settings

Phases 8 and 9 added fourteen environment variables and one workflow — registering a local
git repository — that has no analogue anywhere else in this codebase. Both are described
here; both are also in [`.env.example`](../.env.example) with the same wording.

Three things about *settings as a whole* also belong here, because none of them had anywhere
else to live: the six rate-limiting variables the remediation wave added (§11.4), the fifteen
Phase 4 and Phase 6 settings that were absent from every document until the audit that
preceded that wave found them (§11.5), and the seven `ML_*` settings Phase 11 added (§11.6),
whose installation requirement is in [§1.2](#12-bootstrap) and whose behaviour is in
§6.7.

### 11.1 Registering and scanning a local repository

Developer Intelligence reads repositories **on the machine the backend runs on**, through the
`git` CLI. There is no hosted account to connect and nothing leaves the machine.

#### What you need

| Requirement | Notes |
| --- | --- |
| `git` on `PATH` **for the backend process** | Not for your shell. The backend starts `git` as a subprocess, so it inherits the server's environment. A `git` you installed in a shell profile the server never sources is not a `git` the server can find |
| An absolute path to a git work tree | Relative paths are resolved and stored as absolute, but you should pass the absolute one |
| An authenticated session | Every route requires a bearer token and `analytics.read` |

#### The flow

```bash
# 1. register — the server validates the path and proves it is a work tree
#    BEFORE writing anything, then stores the RESOLVED absolute path
curl -X POST http://localhost:8000/api/v1/developer/repositories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"local_path": "E:/Nexo", "name": "Nexo"}'

# 2. scan — synchronous. ?full=false (the default) reads only what landed
#    since the stored high-water mark
curl -X POST "http://localhost:8000/api/v1/developer/repositories/$ID/scan" \
  -H "Authorization: Bearer $TOKEN"
```

Or press **Register repository** and **Scan** on `/developer`, which is the same two calls.

#### The rules you cannot see from the HTTP surface

- **Validation happens before the write, not after it.** A row pointing at a directory that
  is not a repository would fail on every future scan and would already be on the dashboard
  by the time anybody found out. So the server resolves the path, checks for a `.git` entry,
  and — when `DEVELOPER_PATH_ALLOWLIST` is configured — proves it is under one of the roots
  *before* storing.
- **A bare `git init` with no commits registers successfully.** It is the first thing a user
  does with this feature, and refusing it would tell them their new project does not exist.
- **One path may be registered once per account** (`409`). Two *accounts* registering the
  same directory is allowed: it is a local directory and both may legitimately watch it.
  The account is capped at `DEVELOPER_MAX_REPOSITORIES`.
- **`PATCH` cannot move `local_path`.** The path is the row's identity and the one field
  checked against the filesystem. To repoint a repository, register the new one and remove
  the old.
- **The scan is idempotent.** Commits are upserted on `(repository_id, commit_hash)`, so a
  rescan of an unchanged repository reports `commits_discovered` equal to what git returned
  and `commits_added` of `0`. **That gap is the proof the upsert worked, not a sign
  anything went missing.**

#### A failed scan is a row, not an exception

Every scan is wrapped, so a deleted directory, a corrupt `.git`, an unreadable network share
and a git process that hangs past its timeout all come back as `git_scan_runs` with
`status: 'error'` and a human sentence in `error`. The route answers **200**.

When you are debugging a repository that will not read, this is the sequence:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/developer/repositories/$ID"
# → last_scan_status, last_scan_error, last_scanned_at
```

Then work **down the stack**, because each answer rules out a layer:

| Check | Rules out |
| --- | --- |
| `last_scan_error`'s sentence | The request never reached git. It is the engine's own message |
| `git --version` in a shell with the backend's `PATH` | git is not installed for the process that matters |
| Run the exact path by hand from the backend's working directory | A relative-path or drive-letter problem the server's cwd would explain |
| Is `DEVELOPER_PATH_ALLOWLIST` set? | The path is outside every configured root — the error sentence says so |

#### The two caveats that will otherwise surprise you

- **After a history rewrite — a rebase, `filter-branch`, a force-push — pass
  `?full=true`.** The default scan is incremental (`git log --since` the stored
  `latest_commit_at`), because that is what keeps a rescan cheap and idempotent. After a
  rewrite that mark points at a commit that no longer exists, and only a full read can
  recover from it. Nothing detects the situation automatically. This is the single most
  common "my counts dropped after a rebase" report, and `?full=true` is the answer.
- **`maintenance_activity` reads at its ceiling, not low.** Migration `0008` deliberately creates
  **no** `git_commit_files` table — it would grow to millions of rows on a mature codebase to
  answer questions the phase does not ask — so no per-file history exists for the metric to
  consult. The shipped path does two things about that, and the second is the one that surprises
  people. A commit that touched at least one file reports the single placeholder path
  `<file names are not stored per commit>` rather than an empty file list, and the service calls
  `metrics.maintenance_activity` with `last_touched_before=None`. With no history supplied, every
  touched file counts as quiet, so **every recorded commit that changed a file is counted** —
  the metric's maximum, not a fraction of it. The metric says so in its own user-facing sentence:
  *"…measured without the preceding 90 days of file history, so every touched file counts as
  quiet."* Read it as "commits that touched something, on a record with no file-level history",
  which is what it is. The only commits that do not count are those with `files_changed = 0`.
  Earlier versions of this guide and both phase reports described the opposite behaviour ("reads
  low"), which was wrong twice over: the old code would have reported a measured **zero**, and
  the code that replaced it reports the ceiling.

#### Windows

NEXUS runs a `SelectorEventLoop` on every platform, because psycopg's async driver needs
`loop.add_reader` and asyncio's Windows default does not provide it. But on Windows a
`SelectorEventLoop` raises `NotImplementedError` from `subprocess_exec` — it has no
subprocess transport at all. The git engine detects this (a **class** test, not a trial
call, so no unrelated exception is caught and misreported as a repository problem) and runs
the *same* call on a private `ProactorEventLoop` from a worker thread.

The fallback is deliberately the same function, so the timeout, the output byte ceiling, the
kill and the stderr sanitiser all still apply. The cost is one thread hop per git
invocation. On POSIX none of it runs.

### 11.2 The environment variables

Fourteen, all additive, all with the setting name in uppercase and the field name in
lowercase on `Settings`.

#### Developer (Phase 8)

| Variable | Default | What it controls |
| --- | --- | --- |
| `DEVELOPER_GIT_TIMEOUT_SECONDS` | `30` | Wall-clock budget for one `git` invocation. The subprocess is killed when this elapses, so a hung repository becomes an error row rather than a request that never returns |
| `DEVELOPER_MAX_COMMITS_PER_SCAN` | `2000` | Backstop against a repository whose entire log is new to us. The scan is incremental, so this is not the expected volume |
| `DEVELOPER_MAX_REPOSITORIES` | `100` | How many paths one account may register. Each is a directory the server will read on demand |
| `DEVELOPER_DEFAULT_WINDOW_DAYS` | `30` | The window used when a request names no dates. A month, not a week: a week of commits cannot distinguish a habit from an off week |
| `DEVELOPER_MAX_WINDOW_DAYS` | `366` | Hard ceiling on any requested window. Every windowed aggregate scans the owner's whole commit history, so an unbounded range is the one query shape these indexes cannot serve |
| `DEVELOPER_ACTIVITY_GRANULARITY_DEFAULT` | `day` | Bucket size for the activity series. One of `day`, `week`, `month`, validated **where it is read** rather than here — an unknown bucket size is a request the caller can be told about, whereas a process that refuses to start takes the whole app down over one analytics preference |
| `DEVELOPER_PATH_ALLOWLIST` | `""` | Comma-separated roots under which a repository may be registered. Empty means any readable absolute path that validates as a git work tree, which is the right default for a local-first application. Set it in a shared deployment to stop the server reading an arbitrary path at all |

#### Learning and career (Phase 9)

| Variable | Default | What it controls |
| --- | --- | --- |
| `LEARNING_DEFAULT_WINDOW_DAYS` | `30` | Same argument as `DEVELOPER_DEFAULT_WINDOW_DAYS`: a skill level is only ever described alongside how much was recorded inside the window |
| `LEARNING_MAX_WINDOW_DAYS` | `366` | Hard ceiling on any requested window |
| `LEARNING_MAX_GOALS` | `200` | How many goals one account may keep. **Archived goals still count** — deleting them would delete the record the user kept them for |
| `LEARNING_MAX_SKILLS` | `100` | How many skills one account may keep. The skills list is the input to every gap calculation, so this cap is also what bounds that computation per request |
| `LEARNING_MIN_EVIDENCE_FOR_ESTIMATE` | `3` | Below this many activities in the window, NEXUS offers **no** level estimate at all. This is a **refusal**, not a low-confidence badge: a thin sample shown with a "low confidence" label is still a claim, whereas a stated refusal is not |
| `CAREER_MAX_EVIDENCE` | `500` | Ceiling on rows in one career-evidence list. Everything on a profile is user-supplied or user-approved, so this is a rendering bound rather than a correctness one |
| `CAREER_STALE_INACTIVE_DAYS` | `21` | After this many days with no recorded activity, a target skill counts as dormant and is eligible for a `REVIVE_TARGET_SKILL` nudge. Three weeks is roughly one review cycle: long enough that someone deep in a project is not nagged, short enough that a habit has visibly lapsed |

#### Two of these deserve a warning before you change them

- **`DEVELOPER_PATH_ALLOWLIST` is a security control, not a preference.** Empty is safe only
  because NEXUS is local-first and bound to loopback. In any shared deployment, an empty
  allowlist lets any authenticated account ask the server to run `git` against any directory
  it can read. Set it.
- **`LEARNING_MIN_EVIDENCE_FOR_ESTIMATE` is not a tuning knob.** Lowering it to `1` makes
  every tracked skill with a single recorded activity carry a number NEXUS derived, shown
  with the same weight as one derived from forty. The default of `3` is the smallest sample
  the phase considered worth inferring from at all.

### 11.3 Testing the git engine

The git engine is the only code in NEXUS that talks to another program, so its tests are
structured differently from everything else:

| File | What it does |
| --- | --- |
| `tests/test_developer_git.py` | Pure argument construction, output parsing, the timeout/kill path, the sanitiser, the Windows loop-detection. No repository and no subprocess |
| `tests/test_developer_git_integration.py` | Creates real temporary git repositories, commits into them, and asserts the engine reads what git wrote. Needs `git` on `PATH` and is the slowest Phase 8 file |
| `tests/test_developer_metrics.py` | Every formula, asserted to the exact value, with **no database and no `.git` directory** — because `metrics.py` is pure |

If you change `metrics.py`, the pure suite is the one that will catch you in under a second.
If you change `git.py`, the integration file is the only thing that can tell you the change
is real.

### 11.4 Rate limiting

Six settings, added by the final remediation wave, all read by `RateLimitMiddleware` in
`app/core/middleware.py` and all documented in [`.env.example`](../.env.example).

| Variable | Default | What it controls |
| --- | --- | --- |
| `RATE_LIMIT_ENABLED` | `true` | Master switch for the in-process limiter |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Length of the fixed window every counter is measured over |
| `RATE_LIMIT_GENERAL_MAX_REQUESTS` | `600` | Requests one client address may make to **one** route inside a window, for every route except the two below |
| `RATE_LIMIT_CREDENTIAL_MAX_REQUESTS` | `120` | The same budget for `/auth/login` and `/auth/password/forgot` |
| `RATE_LIMIT_MAX_ENTRIES` | `10000` | Ceiling on tracked client/route pairs |
| `RATE_LIMIT_TRUST_FORWARDED_FOR` | `false` | Whether the client address is taken from `X-Forwarded-For` |

Four properties worth knowing before you change any of them:

- **The store is in process memory.** Counters reset when the process restarts and are not
  shared between workers, so behind more than one worker every number here is a *per-worker*
  budget. Set the same values on each one; that is not the same as a global budget.
- **`RATE_LIMIT_CREDENTIAL_MAX_REQUESTS` is deliberately below the bcrypt ceiling.** 120 a
  minute is two attempts a second, where a single bcrypt-12 verification already permits about
  four. Throttling lower does not extend a patient attacker's timeline; it stops a
  *parallelised* guess flood and the address rotation an enumerator would otherwise use.
- **`OPTIONS` is never counted.** A CORS preflight carries no credentials and reaches no
  handler, so counting it would silently halve the attempts a browser client is allowed
  against `/auth/login`.
- **`RATE_LIMIT_TRUST_FORWARDED_FOR` is off by default and that is the safe default.** That
  header is attacker-controlled on any path that does not terminate in a proxy you control;
  honouring it would let a caller mint a fresh bucket per request by rotating the header —
  and behind a real proxy it would put every client in the same bucket instead. Turn it on
  only when NEXUS genuinely sits behind a trusted reverse proxy.

A throttled request answers **429** with the `rate_limited` code in the shared error envelope
and an `X-Request-ID` header like every other response — the limiter sits *below*
`RequestContextMiddleware` precisely so a throttled response is still correlated and still
logged.

### 11.5 Planner and Analytics settings

Fifteen settings from Phases 4 and 6 that existed in `Settings`, worked, and were documented
nowhere. They are in [`.env.example`](../.env.example) and in the README's
[Environment variables](../README.md#environment-variables) group table; they are repeated here
because a developer changing scheduling or scoring behaviour needs to know they exist.

#### Planner (Phase 4)

| Variable | Default | What it controls |
| --- | --- | --- |
| `PLANNER_DEFAULT_TIMEZONE` | `UTC` | IANA zone used when a request does not pass `tz`. Every stored instant is UTC; this only decides which *day boundaries* a planner view spans |
| `PLANNER_DAY_START_HOUR` | `8` | Start of the fallback working window for a user with no availability rules |
| `PLANNER_DAY_END_HOUR` | `20` | End of that same window. `08:00–20:00` is a daytime window, not a working-hours claim — it is what the scheduler assumes when it has been told nothing |
| `PLANNER_MIN_SESSION_MINUTES` | `15` | Shortest block the scheduler will propose. Below this a session is a rounding error that costs a context switch for no useful work |
| `PLANNER_MAX_SESSION_MINUTES` | `240` | Longest single block. Past this the scheduler splits the work rather than proposing one block nobody will sit through |
| `PLANNER_MAX_SUGGESTIONS_PER_TASK` | `3` | Cap per task, so one large task cannot fill the horizon ahead of a task that is due tomorrow |
| `PLANNER_LOOKAHEAD_DAYS` | `30` | How far forward the scheduler searches. Bounded so a request over a large backlog stays a bounded walk of availability rather than a scan |

#### Analytics (Phase 6)

| Variable | Default | What it controls |
| --- | --- | --- |
| `ANALYTICS_PRODUCTIVITY_WEIGHT_COMPLETION` | `30` | Completion's share of the productivity score |
| `ANALYTICS_PRODUCTIVITY_WEIGHT_DEADLINE` | `25` | Deadline pressure's share |
| `ANALYTICS_PRODUCTIVITY_WEIGHT_CONSISTENCY` | `20` | Consistency's share |
| `ANALYTICS_PRODUCTIVITY_WEIGHT_FOCUS` | `25` | Focus's share |
| `ANALYTICS_COMPARISON_WINDOWS` | `7,30,90` | Period lengths offered for period-over-period comparison, as a comma-separated string |
| `ANALYTICS_DEFAULT_RANGE_DAYS` | `7` | Window when a request names no dates. A week is the shortest span that can distinguish a habit from a one-off |
| `ANALYTICS_MAX_RANGE_DAYS` | `366` | Hard ceiling on any requested range. A range query with no bound is the one shape these indexes cannot serve: every aggregate scans the owner's whole history |
| `ANALYTICS_REBUILD_MAX_DAYS` | `180` | Ceiling on `POST /analytics/rebuild`, the *write* path |

#### The four weights are a start-up gate, not a preference

`_validate_productivity_weights` refuses to construct `Settings` unless the four weights sum
to 100 (and none is negative). The productivity score is presented as a percentage, so the
weights are its denominators: a set summing to 90 would report an "80/100" that is really
"80/90", and one summing to 120 would report a score of 100 having awarded 120 points.

There is deliberately **no** silent renormalisation. Rescaling the weights to 100 would hide
that the configured numbers were wrong, and a formula that cannot be argued with is exactly
what the block of four settings in `config.py` exists to prevent. `Settings` is constructed
once at import time via `get_settings()`, so a set that does not add up **refuses process
start** with a message naming all four values and the required total — you see it on the
console at startup, not as a surprising percentage on a dashboard, and the rest of the
application never comes up at all. See the README's
[`Settings` fails validation](../README.md#settings-fails-validation) entry for that message.

`ANALYTICS_COMPARISON_WINDOWS` behaves the opposite way on purpose: unparsable entries are
**dropped** rather than raising, because they feed a list of suggested period lengths and a
typo in one of them should cost the user that suggestion rather than stop the process. An
empty result is still honest — it renders as "no comparison periods" instead of as a
fabricated default.

### 11.6 ML settings (Phase 11)

Seven settings, all additive, all documented with the same wording in
[`.env.example`](../.env.example). They configure the trained intent classifier that Phase
11 put inside the API process; see
[`architecture.md` §20](architecture.md#20-phase-11--ml-integration) for what they govern.

| Variable | Default | What it controls |
| --- | --- | --- |
| `ML_ENABLED` | `true` | Master switch. Off means NEXUS never loads the Phase 10 checkpoint: every other route keeps working and the two `/ml` endpoints answer 503 `ml_unavailable` |
| `ML_MODEL_PATH` | `""` | Directory holding the Phase 10 `final/` checkpoint. Empty resolves the Phase 10 default — `<backend>/ml/artifacts/small-model/final` — relative to the **repository**, so a moved checkout keeps working. Deployment configuration, deliberately not settable per request |
| `ML_DEVICE` | `auto` | `auto` \| `cpu` \| `cuda`. `auto` uses CUDA when this build of torch sees a GPU and CPU otherwise. `cuda` on a machine without one is a **start-up failure, not a silent downgrade** |
| `ML_CONFIDENCE_THRESHOLD` | `0.90` | Confidence a prediction must reach before NEXUS will name a service for it. See the warning below |
| `ML_MAX_INPUT_CHARS` | `2000` | Hard ceiling on submitted text, enforced before the model sees it. The model was trained at a 128-token context, so this is a rendering and cost bound well above it, not a way to tune accuracy |
| `ML_REJECT_CREDENTIALS` | `true` | Refuse credential-shaped text (a live API key, a private key block, a bearer token) before it is classified |
| `ML_FAIL_FAST` | `false` | When true, a checkpoint that will not load refuses process start instead of degrading to a reportable "unavailable" state |

Four of these deserve the warning before, not after, you change them.

- **`ML_CONFIDENCE_THRESHOLD` is an integration threshold, not a calibrated probability.**
  It was chosen by re-running the checkpoint over the 420-row held-out split and measuring
  the trade directly: at 0.90 NEXUS keeps 95.2% of utterances while lifting precision on
  accepted requests from 0.9738 to 0.9900, rejecting 7 of the 11 errors. At 0.99 it refuses
  more than 40% of requests to buy nothing at all. It was measured on synthetic,
  template-generated text, so real user input will be **less** confident and **less** often
  right — lowering the threshold makes that worse, not better. Raise it only knowing you are
  trading coverage for precision.
- **`ML_CONFIDENCE_THRESHOLD` does not stop confident mistakes.** On deliberately
  adversarial phrasings the classifier was wrong 14 times out of 56, and five of those
  misses were predicted at 0.90 or above. The threshold is a routing guard, not an accuracy
  defence — see [§6.7](#67-the-phase-11-suite-and-its-fourteen-xfails).
- **`ML_MAX_INPUT_CHARS` is a bound with a reason.** The validator refuses a value above
  10 000 as well as a non-positive one, because the trained context is 128 subword tokens
  and text longer than that is truncated before the model sees it: a larger bound would
  accept a large request body and charge for it while changing nothing.
- **`ML_REJECT_CREDENTIALS=false` turns off a screen, not a filter.** With it off, an
  utterance carrying a live secret reaches the classifier, and the classifier is a neural
  network with no promise not to. Turn it off only if you routinely phrase account requests
  in a way the detector reads as a secret — which is the one case the setting exists for.

### 11.7 Verifying the ML environment

```bash
# the Phase 11 suite — five files, needs torch and the checkpoint
python -m pytest tests/test_ml_integration_*.py

# is a checkpoint on this machine at all?
python -c "from app.core.config import get_settings; \
           s = get_settings(); print(s.ml_resolved_model_path, s.ml_checkpoint_exists)"
```

`ml_checkpoint_exists` is `False` on any checkout that has not run the Phase 10 training
pipeline, because `backend/ml/artifacts/` is gitignored. That is a supported state, not a
broken install — but it is also why a fresh clone cannot exercise the `/ml` routes without
first training a checkpoint (§1.1). The live check is `GET /api/v1/ml/status`, which answers
200 either way and names the reason.

---

## See also

| Document | Contents |
| --- | --- |
| [`../README.md`](../README.md) | Prerequisites, quick start, environment variables, the command catalogue, troubleshooting, roadmap |
| [`architecture.md`](architecture.md) | Layering rationale, request lifecycle, error contract, auth design, persistence, decisions and the cost each one accepts |
| [`api-conventions.md`](api-conventions.md) | Base URL and versioning, the error envelope and its code table, request ids, pagination, the endpoint checklist |
| [`specifications/phase-8-developer-report.md`](specifications/phase-8-developer-report.md) | What Phase 8 shipped and what was actually executed |
| [`specifications/phase-9-learning-career-report.md`](specifications/phase-9-learning-career-report.md) | What Phase 9 shipped and what was actually executed |
| [`specifications/phase-11-ml-integration.md`](specifications/phase-11-ml-integration.md) | The Phase 11 serving boundary: `app/ml/`, the two endpoints, the configuration surface and the label contract |
| [`specifications/phase-11-report.md`](specifications/phase-11-report.md) | What Phase 11 executed: the threshold measurements, the generalisation results including the 75.0%, and the limits of each |
