# NEXUS

**Personal Intelligence & Decision Platform** — a local-first, single-user platform for
projects, planning, knowledge, analytics and machine learning. It runs entirely on your
own machine and is never deployed to a cloud. No paid APIs, no paid services.

The one networked step anywhere in the project is Phase 10's **first and only**
model download: `microsoft/deberta-v3-base` is fetched from Hugging Face once, by
the offline `train-small` stage. That is a build-time step driven by `make`, not a
service the application calls: no process under `backend/app/` imports
`backend/ml/`, and nothing the application serves has left this machine.

---

## Status: Phases 1 through 9 are delivered

Phase 1 built the technical foundation and Phase 2 turned authentication into a real
account system: persistent device sessions, a password policy, password recovery,
role-based permissions, an audit trail, and a five-tab settings surface. Phases 3 to 9
then made the module surface real, one module per phase. What exists today:

| Area | State |
| --- | --- |
| Repository layout, tooling, lint/format rules | Done |
| FastAPI backend skeleton, `/api/v1` routing, layered architecture | Done |
| JWT auth (register / login / refresh / logout / me) with `users` table | Done |
| Persistent device sessions — one row per browser, revocable individually or in bulk | Done |
| Password policy, password change, and password reset (no mail service required) | Done |
| Role-based permissions (`user` / `admin`) with a fail-closed `require_permission()` | Done |
| Security audit trail (`audit_logs`, 12 event types, best-effort writes) | Done |
| Alembic migration pipeline | Done — ten revisions, `0001_initial_create_users` → `0010_learning_career_integrity`, a single linear head |
| PostgreSQL 16 | The Compose stack is declared and statically validated but has **never** been executed by `docker compose`. A **native** PostgreSQL 16 runs in the development environment and backs every database-backed test |
| React + TypeScript frontend: router, app shell, design system, theming | Done |
| Frontend design-system primitives, several hand-rolled (see below) | Done |
| Structured logging, `X-Request-ID` correlation, shared error envelope | Done |
| Health/readiness endpoints, Swagger/ReDoc | Done |
| Projects, Tasks, Planner, Knowledge, Analytics, Risks, Recommendations (Phases 3–7) | Live — migrations `0003`–`0007`, backend routes and real pages |
| Developer, Learning, Career (Phases 8 and 9) | Live — see [Phase 8 and Phase 9](#phase-8-and-phase-9--developer-learning-and-career) |
| ML training (Phase 10) | **Done** — one model: a `deberta-v3-base` routing classifier over the 14 Nexo intents, trained on CPU and evaluated at 0.9738 accuracy / 0.9737 macro F1 on a 420-row held-out split. `backend/ml/`, a separate package and entry point (`python -m ml.train`); see [Phase 10](#phase-10--ml-training) |
| Search, AI Assistant, Experiments | Designed placeholder pages only |
| Automated tests | **2270 backend collected** (1029 offline, 1241 `integration`) — collection counts, not a pass count; and **645 frontend** in 44 files, all passing |

The backend figure is a **collection** count from `pytest --collect-only` in `backend/`, and
it is labelled that way on purpose. The last full run before the final remediation pass was
2136 passed and 9 failed; the nine were real defects — three of them lost user data — and
they have been fixed by the engineers who own those files. One full run is scheduled once this
pass lands, so nothing here claims a green backend run that has not happened. The frontend
figure *is* a pass count: `npm test` touches no database, was run end to end, and reports 44
files and 645 tests passing. Docker is still not installed on this machine, so
`docker compose up` remains untested — see
[Troubleshooting](#docker-compose-up-fails-before-anything-starts).

**What the audit found, and what this README now says differently.** Between the last phase
report and this line, an audit ran over Phases 1–9 and found real defects: deleting a project
destroyed subtasks that had been moved to another board; `PUT /availability` deleted the user's
whole week and then refused the replacement; a risk deadline was rendered a day late whenever a
detection pass straddled midnight. Phases 3–5 had no dedicated test modules at all. The
feature vector gained a wrapper so its version string sits beside the numeric matrix rather
than inside it, and a repository that has never been scanned no longer appears in it as a row
of zeros. Each of those has a regression test, each is recorded in
[`docs/architecture.md` §18](docs/architecture.md#18-remediation-pass-over-phases-19) and in
both phase reports, and the counts above were recounted rather than carried forward.

**What does not exist yet.** Search, AI Assistant and Experiments are *designed
placeholder pages only*. They render a real module description, the planned capabilities
and the phase in which they ship — but they store nothing, compute nothing, and read no
data. Every metric tile on those pages renders an em dash on purpose; no sample data is
fabricated.

The rest of the routed surface is live. Login, Register, Forgot password and Reset
password call the real auth endpoints; Dashboard polls service health from the API;
Settings carries profile, password, active sessions with per-device revoke and "sign out
everywhere", theme, and a password-protected account deletion. Projects, Project detail,
Tasks, Planner, Month, Knowledge, Note detail, Concept detail, Analytics, Risk center,
Recommendations, Developer, Developer repository, Learning and Career are all backed by
real routes and real tables. The backend serves **140 paths and 187 operations**; the
per-module inventories live in the phase reports and in
[`docs/api-conventions.md`](docs/api-conventions.md).

Two things exist to prove something works rather than to be useful. `POST /api/v1/users/`
is the administrative account listing: it exists so the role → permission wiring has a route
whose refusal is observable end to end, and it is not a product feature. The audit-log
retention setting states a policy that **no job enforces** — nothing is currently pruning
`audit_logs`.

See [Roadmap](#roadmap) for what has not shipped.

---

## Phase 8 and Phase 9 — Developer, Learning and Career

These two phases landed after the original Status table above was written; that table has
since been brought up to date with them. In short:

| Module | State |
| --- | --- |
| **Developer** (`/developer`, `/developer/repositories/:id`) | **Live.** Register a local git repository, scan it, and read commits, branches, changed lines and language. All 15 backend routes and both pages exist |
| **Learning** (`/learning`) | **Live.** Goals, tracked skills, recorded activities, skill gaps. 20 backend routes |
| **Career** (`/career`) | **Live.** Profile, dated records, portfolio evidence. 13 backend routes |

Each has a full backend, a migration (`0008`, `0009`), and real frontend pages. The two
reports are the honest record of what was executed and what could not be:

- [`docs/specifications/phase-8-developer-report.md`](docs/specifications/phase-8-developer-report.md)
- [`docs/specifications/phase-9-learning-career-report.md`](docs/specifications/phase-9-learning-career-report.md)

### The three rules that shape both features

These are not style preferences; they are properties of the code, and they are worth
knowing before you build on either surface.

1. **A commit is evidence, never a verdict.** Git records that a commit object carries an
   author date; it does not record how long anyone worked. So there is no hours figure, no
   focus score and no productivity verdict anywhere on the developer surface — only counts
   of commit objects, of days that carried a commit, and of lines git counted from a diff.
2. **A skill level is the user's, or visibly derived.** A level is either one the person
   set (`user_defined`) or one NEXUS estimated from recorded activities
   (`system_estimate`), and the source is rendered beside the number on every path. The
   wording differs accordingly — *"current self-assessed 2/5"* versus *"current NEXUS system
   estimate of 2/5"* — and an explanation that omits the phrase its source requires cannot
   be constructed. The neutral form of a gap is *"Target 4/5, current self-assessed 2/5.
   NEXUS recorded 6 related learning activities in the last 30 days."* Never *"You are not
   good at X."*
3. **Absence of measurement is a reason, not a zero.** A real `0` renders as `0`. A figure
   that could not be computed is `null` on the wire, `—` on screen, and often carries a
   sentence saying why. Insufficient data reads *"Not enough data yet."*

### Registering a local repository

Developer Intelligence reads repositories **on the machine the backend runs on**, through
the `git` CLI. There is no hosted account to connect.

```bash
# from the repository root
curl -X POST http://localhost:8000/api/v1/developer/repositories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"local_path": "E:/Nexo", "name": "Nexo"}'

# scan it — ?full=false reads only what landed since the last scan
curl -X POST "http://localhost:8000/api/v1/developer/repositories/$ID/scan" \
  -H "Authorization: Bearer $TOKEN"

# or press "Scan" on /developer
```

The path is validated and resolved to an absolute path **before** the row is stored, so a
relative path cannot quietly resolve elsewhere once a different working directory runs the
scan. A bare `git init` with no commits registers successfully — it is the first thing a
user does with this feature.

**A broken repository never breaks NEXUS.** A scan answers **200 whether the read worked or
not**: a deleted directory, a corrupt `.git`, an unreadable network share and a git process
that hangs past its timeout all come back as a scan row with `status: 'error'` and a human
sentence. The row lists in the repository list carrying `last_scan_status` and
`last_scan_error`, so you can see which directories git could not read rather than one of
them silently removing itself.

Two things are worth knowing before you rely on it:

- **After a history rewrite — a rebase, `filter-branch`, a force-push — pass `?full=true`.**
  The default scan is incremental (`git log --since` the stored high-water mark), and after
  a rewrite that mark points at a commit that no longer exists. Nothing detects this
  automatically.
- **The Windows fallback costs a thread hop per git invocation.** NEXUS runs a
  `SelectorEventLoop` everywhere because psycopg needs it, and on Windows that loop cannot
  spawn a subprocess. The git engine detects the condition and runs the *same* call on a
  private `ProactorEventLoop` from a worker thread, so the timeout, output ceiling and kill
  all still apply.

`DEVELOPER_PATH_ALLOWLIST` restricts which roots a repository may be registered under.
Empty (the default) means any readable absolute path that validates as a git work tree,
which is the right default for a local-first application.

### The environment variables these phases added

Fourteen new settings, in [`.env.example`](.env.example) and documented in
[`docs/development.md`](docs/development.md) §11. The five a newcomer is most likely to
change:

| Variable | Default | Effect |
| --- | --- | --- |
| `DEVELOPER_PATH_ALLOWLIST` | `""` (unset) | Comma-separated roots under which a repository may be registered. Unset means any readable absolute path |
| `DEVELOPER_GIT_TIMEOUT_SECONDS` | `30` | Wall-clock budget for one `git` invocation. A hung repository becomes an error row, not a request that never returns |
| `DEVELOPER_DEFAULT_WINDOW_DAYS` | `30` | Window used when a request names no dates |
| `LEARNING_MIN_EVIDENCE_FOR_ESTIMATE` | `3` | Below this many activities, NEXUS offers **no** level estimate at all and says so |
| `CAREER_STALE_INACTIVE_DAYS` | `21` | After this many days with no recorded activity, a target skill counts as dormant and is eligible for a nudge |

**These two phases run no model.** Both produce *feature vectors* —
`developer_features.v1`, `learning_features.v1`, `career_features.v1` — which are named
numbers stamped with a schema version, ready for the Phase 10 trainer. Nothing on these
surfaces is trained, loaded, served or registered, and no endpoint infers anything. Phase 10
is where the training happens; it is a separate package that the API does not import.

---

## Phase 10 — ML training

Phase 10 is the first phase that trains anything, and it is deliberately kept out of the
running application. It lives in `backend/ml/`, has its own entry point, and is driven by
the `make ml-*` targets. **It is complete** — the specifications are
[`docs/specifications/phase-10-architecture.md`](docs/specifications/phase-10-architecture.md)
and
[`docs/specifications/phase-10-training.md`](docs/specifications/phase-10-training.md),
and the run report is
[`docs/specifications/phase-10-report.md`](docs/specifications/phase-10-report.md).

| Model | Runs on | What it is |
| --- | --- | --- |
| `microsoft/deberta-v3-base` | this machine, CPU | **The only trained model in NEXUS.** A routing / intent classifier fine-tuned over the 14 Nexo intents |

### Where Phase 10 actually stands

Done, and done for real: one model, trained and evaluated on this machine.

- **The routing classifier is trained and evaluated.** `microsoft/deberta-v3-base` at
  **184,432,910 parameters**, fine-tuned over the 14 intents on CPU in 615 steps (5 epochs
  over 1,960 training rows, 3,976 s wall clock), scored on the **420-row held-out test
  split: 0.9738 accuracy, 0.9737 macro F1**, with no class below 0.915 F1. Validation
  (420 rows) came in at 0.9810 accuracy / 0.9809 macro F1. The metrics are written by the
  run itself to `backend/ml/artifacts/small-model/metrics.json`, and the full run record —
  seed, dataset checksum, resolved library versions, per-step loss curve — is in
  `training_state.json` beside it. The numbers above are run
  `small-20261003T193530Z-855c5eb8`, over a 2,800-row corpus (200 per intent, split
  1,960 / 420 / 420, zero validation findings, zero leaked keys).
- **The suites pass.** `backend/tests/test_ml_*.py` is **321 passed**, 1 skipped; the
  full backend suite is **2 591 passed**, 1 skipped, 0 failed. The one skip is a
  Windows symlink privilege a Phase 8 test needs, not something deferred.
- **The pipeline reports `PASS` end to end.** `ml/reports/pipeline_summary.json` records all
  three stages — `prepare`, `train-small`, `evaluate` — as `PASSED`, with a verdict of
  `PASS`. The earlier 2,100-row, 4-epoch, 368-step run
  (`small-20261003T124537Z-853c3adc`, 0.9675 accuracy / 0.9674 macro F1 on 308 test rows)
  is superseded, and is quoted here only as history.

The model's job is **routing, not answering**: it decides *which* capability should
handle an utterance. Twelve of the fourteen Nexo intents are answered by the deterministic
services in `app/services/`, which are faster and more reliable than any language model.
The other two, `code_assist` and `deep_reasoning`, have a destination of
`large-model:unavailable` — **NEXUS runs no language model** — and they are trained and
predicted anyway, so the router can recognise a request it cannot serve instead of being
blind to it. `out_of_scope` is a trained class too, so abstention is something the model
can be *right* about. The classifier exists to keep the cheap intents cheap; it does not
displace the rules.

An earlier draft of this phase specified a second trained model as well. It was **removed
from the repository** — not disabled — along with its corpus, config, notebook renderers,
remote driver and pipeline stages. Nothing in this README or in the code refers to it as
though it existed; the report records what was deleted and why.

Phase 10 ends at artifacts. Nothing here loads the model into the running application,
and no route, service or frontend page depends on one. Loading it is Phase 11's job; the
deterministic path in `app/services/` stays the answer whenever a model is absent,
unevaluated or unsure.

```bash
make ml-help          # list every Phase 10 target
make ml-prepare       # build, validate and split the corpus (stdlib only, no GPU)
make ml-train-small   # fine-tune the routing classifier (needs backend/ml/.venv)
make ml-eval          # evaluate it and write the reports
make ml-all           # prepare + train-small + evaluate
```

Phase 10 defines **nine** `ml-*` targets; the five above are the ones a local run uses.
The full set is `ml-help`, `ml-prepare`, `ml-datasets` (an alias for `ml-prepare`),
`ml-validate`, `ml-train-small`, `ml-train-small-resume`, `ml-eval`, `ml-all` and
`ml-test`. `make ml-help` greps that list out of the `Makefile` itself, so the help cannot
drift from the targets. `python -m ml.train` has exactly **three** selectable stages —
`--prepare`, `--train-small` and `--evaluate` — which always run in that order and are
what `ml-all` runs. `--resume` is a modifier rather than a stage, spelled
`make ml-train-small-resume`, and `--dry-run` prints the plan and exits.

Two interpreters are involved and the Makefile picks the right one: the data half is
stdlib-only and runs anywhere, while the training half needs `backend/ml/.venv/`, which
carries the torch wheel the backend venv does not have. Override it with
`make ml-train-small ML_PY=backend/ml/.venv/Scripts/python.exe`.

---

## Architecture at a glance

Everything runs on one machine. There is no external service of any kind that NEXUS
itself calls, and no outbound path in the request path. The only network the project
ever touches is a one-off checkpoint download during `make ml-train-small`, driven by
`make`, off the request path — and the resulting classifier is only written to disk.
Nothing loads it.

```
        ┌────────────────────────── your machine ──────────────────────────┐
        │                                                                  │
        │   Browser ──── :5173 ───► Vite dev server (React 19 + TypeScript) │
        │                              │                                   │
        │                              │ proxies /api and /health          │
        │                              ▼                                   │
        │   Browser ──── :8000 ───► FastAPI (uvicorn)                     │
        │                              │                                   │
        │                              │ SQLAlchemy 2.x async (psycopg 3)  │
        │                              ▼                                   │
        │                         PostgreSQL 16 ──── :5432                │
        │                                                                  │
        │   .env ──► pydantic-settings ──► both processes                  │
        │   docker-compose.yml ──► postgres | backend | frontend           │
        └──────────────────────────────────────────────────────────────────┘
```

Three containers, wired by health checks:

| Service | Published port | Image / build | Starts when |
| --- | --- | --- | --- |
| `postgres` | `${BIND_HOST}:${POSTGRES_PORT}:5432` | `postgres:16-alpine`, named volume `nexus_pgdata` | — |
| `backend` | `${BIND_HOST}:8000:8000` | build `./backend` | `postgres` is healthy |
| `frontend` | `${BIND_HOST}:5173:5173` | build `./frontend` (Vite dev server) | `backend` is healthy |

`BIND_HOST` defaults to `127.0.0.1`, so all three ports are reachable from this machine
only. Widening it to `0.0.0.0` publishes the database, the API and the dev server to every
host on the network, in front of the default `nexus`/`nexus` credentials and the
placeholder `SECRET_KEY`. See [`BIND_HOST`](#environment-variables).

The backend container's start command is `alembic upgrade head && exec python run.py`, so a
fresh volume is migrated before the API answers.

### Repository map

```
Nexo/
├── .env.example            source of truth for every environment variable
├── Makefile                thin wrappers around every dev command
├── docker-compose.yml      postgres + backend + frontend
├── scripts/                bootstrap, db wait, test-db creation, compose check, dev launcher
├── docker/postgres/init/   pg_trgm + unaccent, applied on first init only
├── docs/
│   ├── architecture.md
│   ├── development.md
│   └── api-conventions.md
├── backend/
│   ├── run.py              entrypoint — selects the psycopg-compatible event loop
│   ├── alembic.ini         no credentials; the URL comes from settings
│   ├── migrations/         Alembic env + versions (0001 … 0009, single head)
│   ├── requirements.txt    runtime deps above the DEV MARKER, dev deps below
│   ├── pyproject.toml      ruff configuration
│   ├── pytest.ini          testpaths, asyncio mode, `integration` marker
│   ├── Dockerfile          multi-stage; strips dev deps at the DEV MARKER
│   ├── tests/              pytest suite
│   └── app/
│       ├── main.py         application factory, lifespan, /health and /
│       ├── api/
│       │   ├── router.py              mounts /api/v1
│       │   ├── deps.py                HTTP-layer wiring: session → repo → service
│       │   └── v1/                    19 routers: health, auth, users, projects, tasks,
│       │                              tags, activity, calendar, work_sessions, planner,
│       │                              availability, knowledge, analytics, developer, risks,
│       │                              recommendations, intelligence, learning, career
│       ├── core/
│       │   ├── config.py              typed settings (pydantic-settings)
│       │   ├── exceptions.py         domain errors + shared error envelope
│       │   ├── event_loop.py         SelectorEventLoop on Windows for psycopg
│       │   ├── logging.py            JSON/console logging, redaction, request_id
│       │   ├── middleware.py          X-Request-ID, timing, access log
│       │   ├── permissions.py         Permission enum (11 members), ROLE_PERMISSIONS, gate factory
│       │   ├── security.py            bcrypt + JWT issue/verify, token digests
│       │   └── deps.py                canonical auth dependencies
│       ├── db/{base,session}.py       Base + mixins, async engine and session
│       ├── models/                   user, session, password_reset, audit, project, task,
│       │                              tag, activity, planner, knowledge, analytics, risk,
│       │                              developer, learning, career
│       ├── repositories/             one per model module, SQL only
│       ├── schemas/                  common, health, user, session, security + one per module
│       ├── services/                 auth, session, user, audit + one per module
│       │                             (developer/, learning/, career/ are packages)
│       └── ml/                       Phase 10 ML training — own venv, `python -m ml.train`
│           ├── train.py              the one entry point: --prepare/--train-small/--evaluate
│           ├── validation.py         the data-integrity gate prepare must pass
│           ├── configs/              small_model.toml
│           ├── datasets/             capability harvest, routing corpus, taxonomy, splits
│           ├── preprocessing/        normalisation and redaction
│           ├── training/             config, checkpoints, run manifests
│           ├── scripts/              train_small_local.py — the training loop
│           ├── evaluation/           classifier metrics
│           ├── artifacts/            the trained classifier and its metrics (gitignored)
│           └── reports/              the generated dataset, split and pipeline reports
└── frontend/
    ├── vite.config.ts      dev proxy (:8000), code splitting, Vitest config
    ├── tailwind.config.ts  token → utility mapping
    ├── components.json     shadcn/ui configuration (new-york, lucide)
    ├── eslint.config.js
    ├── Dockerfile          node:22-alpine running the Vite dev server
    └── src/
        ├── main.tsx        React root
        ├── app/            providers, query client, theme provider, auth bootstrap
        ├── routes/         router, layouts, guards, lazy route table
        ├── pages/          one file per route (search, assistant and experiments are placeholders)
        ├── features/       domain logic: auth, health, modules, settings, palette, work,
        │                   planner, knowledge, analytics, risk, developer, learning, career
        ├── components/
        │   ├── ui/         design-system primitives (shadcn-style; some hand-rolled)
        │   ├── layout/     app shell, sidebar, top bar, menus, palette
        │   ├── feedback/   loading / empty / error states, page header
        │   └── brand/      logo
        ├── hooks/          use-command-palette, use-debounce, use-media-query
        ├── lib/            api-client.ts, utils.ts (cn)
        ├── services/       auth, sessions, users, health, errors, work, planner, knowledge,
        │                   analytics, risk, developer, learning
        ├── stores/         Zustand auth, theme and toast stores
        ├── types/          wire types mirroring the backend schemas
        └── index.css       design tokens (HSL channels) + structural helpers
```

---

## Stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Language (backend) | CPython 3.13+ | `requires-python = ">=3.13"` |
| Web framework | FastAPI 0.142 | async, OpenAPI-first |
| ASGI server | uvicorn 0.54 | started through `backend/run.py` |
| ORM | SQLAlchemy 2.1 (async) | `postgresql+psycopg` |
| Driver | psycopg 3.3 | binary wheel; requires a SelectorEventLoop on Windows |
| Migrations | Alembic 1.20 | async engine, URL injected from settings |
| Validation / config | Pydantic 2.13 + pydantic-settings | one typed `Settings` object |
| Auth | PyJWT 2.15 + bcrypt 5.0 | HS256, access + refresh, in-process access-token denylist plus database-backed device sessions |
| Database | PostgreSQL 16 (`postgres:16-alpine`) | `pg_trgm`, `unaccent` enabled on first init |
| Lint / format (backend) | ruff 0.16 | `check` + `format --check`, line length 100 |
| Tests (backend) | pytest 9.1 + pytest-asyncio + httpx | 2270 collected — 1029 run with the database down, 1241 are `integration`-marked and run against PostgreSQL |
| Framework (frontend) | React 19 | function components, StrictMode |
| Language (frontend) | TypeScript 5.7 | `strict`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax` |
| Build / dev server | Vite 7 | dev proxy, manual chunks, Vitest config |
| Routing | react-router-dom 7 | `createBrowserRouter` |
| Server state | TanStack Query 5 | retries, caching, polling |
| Client state | Zustand 5 | auth session, theme, command palette, toasts |
| Styling | Tailwind CSS 3.4 + shadcn/ui conventions | `cva` variants, lucide icons, and a small number of hand-rolled primitives where no Radix package is installed |
| Charts | recharts 2.15 | reserved for the Analytics module |
| Tests (frontend) | Vitest 3.2 + React Testing Library | 645 tests in 44 files |

Production bundle is code-split per route and by vendor group. Current build, uncompressed
`frontend/dist/assets/` sizes, as produced by `npx vite build`: entry chunk `index`
104,155 B, largest vendor chunk `charts` 432,148 B, then `react` 222,425 B, `radix`
113,444 B, `router` 92,238 B and `data` 37,965 B. The largest real page chunk is now
`learning-page` at 67,842 B, followed by `career-page` at 52,538 B, `developer-page` at
35,303 B, `knowledge-page` at 34,610 B and `planner-page` at 32,662 B; `settings-page` is
31,313 B. The shared `module-page` chunk the three remaining placeholder routes render is
2,754 B, and `not-found-page` 2,314 B. A placeholder chunk growing by kilobytes is the
signal that something page-specific has crept into it.

---

## Prerequisites

| Requirement | Version | Needed for |
| --- | --- | --- |
| Python | 3.13+ | backend, migrations, tests, scripts |
| Node.js | 20.19+ (22 LTS recommended) | frontend, tests, build |
| npm | ships with Node | frontend |
| Docker + Compose v2 | recent | the `docker compose` path (optional) |
| PostgreSQL | 16 (or a local 13+) | the non-Docker path |
| GNU make | 4.x | optional; the Makefile is a convenience only |

`make` is not shipped with Windows. Either install it (Git Bash make package, Chocolatey,
Scoop) or run the raw commands listed under [Development commands](#development-commands) —
the Makefile contains nothing the scripts and npm do not already do.

---

## Quick start

### Path A — Docker Compose (everything in containers)

Requires Docker with the Compose v2 plugin, and a `.env` in the repository root — the
compose file passes it to the containers, so `cp .env.example .env` is not optional.

```bash
# from the repository root
cp .env.example .env
docker compose up -d --build
```

Then open:

| What | URL |
| --- | --- |
| Application | http://localhost:5173 |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| Liveness | http://localhost:8000/health |
| Detailed health | http://localhost:8000/api/v1/health |

Those URLs work because the published ports bind to `127.0.0.1` by default
(`BIND_HOST`). Create an account from the **Authorize** button in Swagger, or from the
register page in the UI.

Follow the logs, and stop the stack:

```bash
docker compose logs -f
docker compose down          # keeps the database volume
docker compose down -v       # also drops nexus_pgdata
```

> **This path has never been run.** `docker-compose.yml` was authored on a machine without
> Docker. It is parsed and structurally validated by `scripts/verify_compose.py`, but no
> `docker compose` command has ever executed it. Treat your first `up` as untested, and
> start with `docker compose logs -f`. See
> [Troubleshooting](#docker-compose-up-fails-before-anything-starts).

#### What `docker-compose.yml` interpolates, and what it deliberately does not

`.env` reaches the stack by two different mechanisms, and the difference matters:

| Mechanism | Where | Effect |
| --- | --- | --- |
| `${VAR:-default}` | throughout the file | Interpolated while Compose parses the YAML. The container sees the value written here and nothing else. |
| `env_file: .env` | `backend`, `frontend` | The whole file is handed to the container, so the ~25 settings no `environment:` entry names — `DEBUG`, `APP_NAME`, `OPENAPI_URL`, `JWT_*`, `PASSWORD_*`, `SESSION_ABSOLUTE_LIFETIME_DAYS`, `MAX_ACTIVE_SESSIONS`, `AUDIT_LOG_RETENTION_DAYS`, `DB_POOL_*`, `DB_PROBE_TIMEOUT_SECONDS`, `LOG_*`, `API_V1_PREFIX` — arrive as written. |

`environment:` outranks `env_file:`, so the explicit overrides still win. Only a handful of
values are literals, and each one is deliberate: the container-side port numbers, the
healthchecks, the start commands, and the values that cannot survive into a container —
`POSTGRES_HOST: postgres`, `NEXUS_HOST: 0.0.0.0`, `NEXUS_PORT: 8000`, and the in-network
`VITE_API_BASE_URL` / `VITE_DEV_PROXY_TARGET` values the Vite proxy depends on. Nothing else
in the file is hardcoded. (`$$VAR` is Compose's escape for a literal `$VAR`, which is how the
healthchecks reach `POSTGRES_USER` inside the container.)

`DATABASE_URL` is the one value Compose actively clears: it is set to the empty string so
that the backend assembles the DSN from `POSTGRES_*` via
`Settings.sqlalchemy_database_uri`, with the host as `postgres` and the user and password
percent-encoded. The URL in `.env` points at `127.0.0.1`, which does not resolve inside a
container. If you would rather set a full `DATABASE_URL` of your own for the Compose stack,
remember to percent-encode any reserved character (`@ : / # ? %`) in the credentials — a raw
one produces a DSN the driver cannot parse.

### Path B — local development (hot reload, your own processes)

Requires Python 3.13+, Node 20.19+ and a reachable PostgreSQL 16.

```bash
# from the repository root
python scripts/bootstrap.py
```

The bootstrap script is idempotent and stdlib-only. It verifies Python and Node, checks
the checkout layout, reports whether Docker is available, creates `.env` from
`.env.example` if it is missing, creates `backend/.venv`, installs
`backend/requirements.txt`, runs `npm install` in `frontend/`, and prints the next steps.
Use `--skip-install` to only run the checks and create `.env`.

Then, from the repository root:

```bash
backend/.venv/bin/python scripts/wait_for_db.py          # or Scripts\python.exe; = make db-wait
backend/.venv/bin/python scripts/create_test_database.py  # = make test-db
cd backend && ../backend/.venv/bin/python -m alembic upgrade head
cd .. && ./scripts/dev.sh
```

(With GNU make the first three lines are `make db-wait`, `make test-db`, `make migrate`.)

`scripts/dev.sh` starts the backend and the frontend together against an already-running
PostgreSQL and stops both on Ctrl-C. It accepts `backend`, `frontend`, `both` (default) or
`--help`. On Windows run it from Git Bash or WSL: `bash scripts/dev.sh`.

Run the processes separately if you prefer:

```bash
# terminal 1 — backend, from backend/
python run.py

# terminal 2 — frontend, from frontend/
npm run dev
```

> **Always start the backend with `python run.py` from `backend/`.** A bare
> `uvicorn app.main:app` will start on Windows but cannot reach the database: psycopg's
> async driver needs `loop.add_reader`, which asyncio's default Windows
> `ProactorEventLoop` does not provide. `run.py` selects the correct loop via
> `backend/app/core/event_loop.py`. See [Troubleshooting](#troubleshooting).

---

## Environment variables

`.env.example` in the repository root is the source of truth, and every line of it is
commented. `.env` is git-ignored and **a fresh clone has no `.env`** — the first step of
either quick start above is copying it:

```bash
cp .env.example .env              # macOS / Linux / Git Bash
Copy-Item .env.example .env       # PowerShell
```

`Settings` in `backend/app/core/config.py` also looks for `.env` in `../.env` and
`../../.env`, so the file at the repository root is found regardless of the working
directory. Every variable is case-insensitive.

### Groups

| Group | Variables |
| --- | --- |
| Application | `ENVIRONMENT`, `DEBUG`, `APP_NAME`, `APP_VERSION`, `APP_DESCRIPTION` (shown in the OpenAPI schema and the docs UI), `OPENAPI_URL`, `DOCS_URL`, `REDOC_URL`, `API_V1_PREFIX` (the prefix every versioned route is mounted under; change it and `VITE_API_BASE_URL` has to change with it) |
| Security | `SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` |
| Accounts, sessions and audit (Phase 2) | `PASSWORD_MIN_LENGTH` (default 8, plus uppercase/lowercase/digit/special), `PASSWORD_RESET_EXPIRE_MINUTES` (30), `SESSION_ABSOLUTE_LIFETIME_DAYS` (30), `MAX_ACTIVE_SESSIONS` (20), `AUDIT_LOG_RETENTION_DAYS` (400 — **declared, not enforced**; no pruning job exists) |
| Rate limiting | `RATE_LIMIT_ENABLED` (true), `RATE_LIMIT_WINDOW_SECONDS` (60), `RATE_LIMIT_GENERAL_MAX_REQUESTS` (600 per route per address per window), `RATE_LIMIT_CREDENTIAL_MAX_REQUESTS` (120, for `/auth/login` and `/auth/password/forgot`), `RATE_LIMIT_MAX_ENTRIES` (10000 — the backstop that keeps the in-memory store from becoming the leak it prevents), `RATE_LIMIT_TRUST_FORWARDED_FOR` (false — turn it on **only** behind a trusted reverse proxy) |
| Backend server (read by `backend/run.py`) | `NEXUS_HOST`, `NEXUS_PORT`, `NEXUS_RELOAD` |
| CORS | `CORS_ORIGINS` (comma-separated, no trailing slashes) |
| Database | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`, `DATABASE_URL`, `TEST_DATABASE_URL`, `DB_ECHO`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE`, `DB_PROBE_TIMEOUT_SECONDS` |
| Planner (Phase 4) | `PLANNER_DEFAULT_TIMEZONE` (`UTC` — the zone that decides which *day boundaries* a view spans; every stored instant stays UTC), `PLANNER_DAY_START_HOUR` (8), `PLANNER_DAY_END_HOUR` (20) — the fallback working window for a user with no availability rules, `PLANNER_MIN_SESSION_MINUTES` (15), `PLANNER_MAX_SESSION_MINUTES` (240), `PLANNER_MAX_SUGGESTIONS_PER_TASK` (3), `PLANNER_LOOKAHEAD_DAYS` (30) |
| Analytics (Phase 6) | `ANALYTICS_PRODUCTIVITY_WEIGHT_COMPLETION` (30), `ANALYTICS_PRODUCTIVITY_WEIGHT_DEADLINE` (25), `ANALYTICS_PRODUCTIVITY_WEIGHT_CONSISTENCY` (20), `ANALYTICS_PRODUCTIVITY_WEIGHT_FOCUS` (25) — **these four must sum to 100 or the process refuses to start**; see [`Settings` fails validation](#settings-fails-validation). Also `ANALYTICS_COMPARISON_WINDOWS` (`7,30,90`), `ANALYTICS_DEFAULT_RANGE_DAYS` (7), `ANALYTICS_MAX_RANGE_DAYS` (366), `ANALYTICS_REBUILD_MAX_DAYS` (180) |
| Logging | `LOG_LEVEL`, `LOG_JSON`, `LOG_FILE`, `LOG_REQUEST_BODY`, `SLOW_REQUEST_MS` |
| Frontend — read by the app (only `VITE_*` reaches the browser) | `VITE_API_BASE_URL` |
| Frontend — dev server only | `VITE_DEV_PROXY_TARGET` — server-side, read by `frontend/vite.config.ts`; it is never bundled into the browser build |
| Frontend — **reserved, read by no code** | `VITE_API_SERVER_URL`, `VITE_APP_NAME`, `VITE_ENABLE_COMMAND_PALETTE` — declared in `frontend/src/vite-env.d.ts` and in `.env.example`, but no module in `frontend/src` reads them. They are kept so the names stay stable for whoever wires those features up; changing them has no effect today. |
| Docker Compose | `BIND_HOST` (see below), `POSTGRES_CONTAINER_NAME`, `POSTGRES_VOLUME_NAME`, `BACKEND_CONTAINER_NAME`, `FRONTEND_CONTAINER_NAME` |
| Developer intelligence (Phase 8) | `DEVELOPER_GIT_TIMEOUT_SECONDS` (30), `DEVELOPER_MAX_COMMITS_PER_SCAN` (2000), `DEVELOPER_MAX_REPOSITORIES` (100), `DEVELOPER_DEFAULT_WINDOW_DAYS` (30), `DEVELOPER_MAX_WINDOW_DAYS` (366), `DEVELOPER_ACTIVITY_GRANULARITY_DEFAULT` (`day`), `DEVELOPER_PATH_ALLOWLIST` (unset) |
| Learning and career (Phase 9) | `LEARNING_DEFAULT_WINDOW_DAYS` (30), `LEARNING_MAX_WINDOW_DAYS` (366), `LEARNING_MAX_GOALS` (200), `LEARNING_MAX_SKILLS` (100), `LEARNING_MIN_EVIDENCE_FOR_ESTIMATE` (3), `CAREER_MAX_EVIDENCE` (500), `CAREER_STALE_INACTIVE_DAYS` (21) |

`BIND_HOST` (default `127.0.0.1`) prefixes every published port mapping in
`docker-compose.yml` — the database, the API and the Vite dev server. The default keeps all
three reachable from this machine alone. Widening it to `0.0.0.0` or a LAN address
publishes them to every host on the network, in front of the default `nexus`/`nexus`
credentials, a placeholder `SECRET_KEY` and `DEBUG=true`. Only do that on a network you
trust as much as the machine itself.

`DB_PROBE_TIMEOUT_SECONDS` (default 3) is the wall-clock budget for the `SELECT 1` health
probe in `backend/app/db/session.py`. `DB_POOL_TIMEOUT` only bounds the wait for a free
connection, not the TCP handshake behind it, so a filtered port or a wedged server would
otherwise stall the probe until the OS TCP timeout.

One caveat on `VITE_DEV_PROXY_TARGET`: Vite resolves `.env` files relative to its own root,
which is `frontend/`, not the repository root. On the host the default
`http://localhost:8000` therefore applies and that is the correct value anyway; it is
Compose — which injects the variable straight into the frontend container's environment —
that moves the proxy to `http://backend:8000` inside the network.

### The handful a newcomer will actually change

| Variable | Why |
| --- | --- |
| `SECRET_KEY` | Signs every JWT. The `.env.example` value is a placeholder. Generate a real one with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Must match the server you actually run. `docker-compose.yml` passes them to the image; a local PostgreSQL uses your existing role. |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Keep `127.0.0.1`, not `localhost` — see below. Move `POSTGRES_PORT` if a native server already owns 5432. |
| `DATABASE_URL` | Overrides the assembled URL entirely. Empty it out and `Settings.sqlalchemy_database_uri` builds the DSN from `POSTGRES_*`, percent-encoding the user and password. Compose clears it for you, because the value in `.env` points at `127.0.0.1`, which does not resolve inside the container. |
| `NEXUS_RELOAD` | `true` for local development, `false` anywhere else. Compose forces it to `false`. |

### `SECRET_KEY` in production

`app/core/config.py` refuses to start a process with `ENVIRONMENT=production` when
`SECRET_KEY` is still `dev-insecure-change-me`, and also refuses `DEBUG=true` in
production. Generate a real key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Why `127.0.0.1` and not `localhost`

On Windows `localhost` resolves to `::1` first, and psycopg's async driver only speaks
IPv4. With `localhost` the application hangs instead of failing fast. Use `127.0.0.1` in
`DATABASE_URL`, `TEST_DATABASE_URL` and `POSTGRES_HOST`. The helper scripts rewrite
`localhost` to `127.0.0.1` for you; the application does not, because it should never
have to.

---

## Development commands

### Make targets

`make` is a thin wrapper: each target runs a real command, and the logic lives in the
scripts and npm scripts. `make` with no argument lists them.

| Target | Effect |
| --- | --- |
| `make help` | List every target with its one-line description |
| `make install` | Run `scripts/bootstrap.py` (venv, `.env`, `npm install`) |
| `make bootstrap` | Alias for `make install` |
| `make up` | `docker compose up -d --build`, then print the frontend and docs URLs |
| `make down` | `docker compose down` (keeps the volume) |
| `make logs` | `docker compose logs -f` |
| `make migrate` | `alembic upgrade head` |
| `make migrate-down` | `alembic downgrade -1` |
| `make revision m="add widgets table"` | `alembic revision -m "<message>"` |
| `make db-wait` | `scripts/wait_for_db.py --timeout $(TIMEOUT)` — block until PostgreSQL accepts connections. `make db-wait TIMEOUT=120` to wait longer. |
| `make test-db` | `scripts/create_test_database.py` — create the `nexus_test` database. Never drops anything by default; pass `TEST_DB_FLAGS=--drop` to rebuild it, which is destructive. |
| `make test` | `test-backend` then `test-frontend` |
| `make test-backend` | `pytest` in `backend/` (all 2270 tests — the 1241 `integration` ones included, and they need PostgreSQL) |
| `make test-frontend` | `npm run test` in `frontend/` |
| `make lint` | ruff check, ruff format --check, eslint, tsc -b |
| `make backend` | `scripts/dev.sh backend` |
| `make frontend` | `scripts/dev.sh frontend` |
| `make ml-help` | List the Phase 10 ML targets — see [Phase 10](#phase-10--ml-training) |
| `make ml-prepare` | Build, validate and split the ML datasets (stdlib only, no GPU) |
| `make ml-all` | `ml-prepare` → `ml-train-small` → `ml-eval` (the local Phase 10 pipeline) |
| `make clean` | Remove caches, `frontend/dist`, `frontend/coverage`, `*.tsbuildinfo` |

Those three are the common `ml-*` targets, not the whole set. Phase 10 defines nine —
`ml-help`, `ml-prepare`, `ml-datasets`, `ml-validate`, `ml-train-small`,
`ml-train-small-resume`, `ml-eval`, `ml-all` and `ml-test` — all of them listed in
[Phase 10](#phase-10--ml-training).

`db-wait` and `test-db` exist so the two helper scripts no longer have to be invoked by
hand; `scripts/dev.sh` already waits for the database itself, but only for 15 seconds.

The interpreter is detected automatically. `PY` probes for
`backend/.venv/Scripts/python.exe` first and falls back to `backend/.venv/bin/python`, so
the same Makefile works on a Windows virtualenv and a POSIX one. Override it only when you
want a different interpreter — the path is always relative to the repository root:

```bash
make test-backend PY=backend/.venv/Scripts/python.exe
make migrate PY=/usr/bin/python3.13
```

`NPM` is overridable the same way (`make test-frontend NPM=pnpm`).

### Raw commands

Backend — run from `backend/`:

```bash
python run.py                                   # dev server, honours NEXUS_HOST/PORT/RELOAD
python -m pytest                                # 2270 collected: 1029 offline plus 1241 integration (need PostgreSQL)
python -m pytest -m "not integration"           # 1029 tests, no database needed
python -m ruff check .
python -m ruff format --check .
python -m alembic upgrade head                  # apply migrations
python -m alembic downgrade -1                  # roll back one migration
python -m alembic revision -m "add widgets table"
python -m alembic revision --autogenerate -m "add widgets table"
python -m alembic history --verbose
```

Use `backend/.venv/bin/python` (or `backend\.venv\Scripts\python.exe`) unless your
environment already has the dependencies installed.

Frontend — run from `frontend/`:

```bash
npm run dev            # Vite dev server on :5173
npm run build          # tsc -b && vite build
npm run preview        # serve dist/ on :4173
npm test               # vitest run (645 tests in 44 files)
npm run test:watch     # vitest
npm run test:coverage  # vitest run --coverage
npm run lint           # eslint .
npm run typecheck      # tsc -b
```

Scripts — run from the repository root:

```bash
python scripts/bootstrap.py [--skip-install]
python scripts/wait_for_db.py [--url URL] [--timeout 60] [--interval 1] [--quiet]
python scripts/create_test_database.py [--url URL] [--drop]
python scripts/verify_compose.py [--strict]    # needs PyYAML; not a project dependency
bash scripts/dev.sh [both|backend|frontend]
```

### What the scripts actually do

| Script | Behaviour |
| --- | --- |
| `scripts/bootstrap.py` | Stdlib only. Checks Python ≥ 3.13, Node ≥ 20 and npm, verifies the checkout, reports Docker availability, creates `.env` from `.env.example` (never overwriting an existing one), creates or reuses `backend/.venv`, `pip install -r backend/requirements.txt`, `npm install`, prints next steps. |
| `scripts/wait_for_db.py` | Polls `SELECT 1` until the configured database accepts connections. Default timeout 60 s, interval 1 s, per-attempt connect timeout 5 s. Turns libpq's error into a remedy (role missing / database missing / bad password / nothing listening). Passwords are redacted from every message, including credentials libpq echoes back inside errors. |
| `scripts/create_test_database.py` | Creates the `nexus_test` database if missing, connecting to the `postgres` maintenance database in autocommit mode. Then tries to enable `pg_trgm` and `unaccent`; a missing contrib module is a warning, not an error. `--drop` recreates it (destructive). |
| `scripts/dev.sh` | Starts `python run.py` and `npm run dev` together, waits for the database for up to 15 s (non-fatal), and stops both on Ctrl-C. If either server exits, it takes the other one down too. Uses `taskkill /T` on Windows because `npm run dev` orphans `node.exe` and `esbuild.exe`. |
| `scripts/verify_compose.py` | Parses `docker-compose.yml` with PyYAML and checks: Compose v2 syntax (no `version:` key), project `name: nexus`, exactly the three expected services, every `build.context` is a real directory containing the Dockerfile, every bind-mounted host path exists, and every `${VAR}` is documented in `.env.example` (14 variables at the time of writing). `--strict` additionally warns about `.env.example` entries compose never uses. Static only — it cannot tell you that `docker compose up` works. |
| `scripts/_common.py` | Shared helpers: `.env` parsing (hand-written so `bootstrap.py` works on a bare interpreter), URL resolution/redaction, venv interpreter discovery, lazy `psycopg` import. Not a command. |

---

## API documentation

| What | URL |
| --- | --- |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| Service metadata | http://localhost:8000/ |
| Liveness (no database) | http://localhost:8000/health |
| Detailed health | http://localhost:8000/api/v1/health |

### Endpoints

The API serves **140 paths and 187 operations** today, across nineteen routers mounted
under `/api/v1` by `backend/app/api/v1/router.py`.

The table below is the **Phase 2 slice** — health, auth and users — which is the part
this README documents route by route. It is 17 operations across 16 paths of that
total. Everything Phases 3 through 9 added (projects, tasks, tags, activity, calendar,
work sessions, planner, availability, knowledge, analytics, developer, risks,
recommendations, intelligence, learning and career) is inventoried in the phase reports
and, by convention, in [`docs/api-conventions.md`](docs/api-conventions.md).

| Method | Path | Auth | Success |
| --- | --- | --- | --- |
| `GET` | `/` | no | 200, service metadata and endpoint links |
| `GET` | `/health` | no | 200 `{"status":"ok"}` — never touches the database |
| `GET` | `/api/v1/health` | no | 200, app/version/environment/database status + latency/uptime/timestamp |
| `POST` | `/api/v1/auth/register` | no | 201, `UserRead` — 409 if the email **or** the username is taken |
| `POST` | `/api/v1/auth/login` | no | 200, `TokenPair` — opens a device session. 429 `rate_limited` once an address exhausts its credential budget |
| `POST` | `/api/v1/auth/refresh` | no | 200, `TokenPair` — single-use rotation on the same session row |
| `POST` | `/api/v1/auth/logout` | optional | 204 no content |
| `POST` | `/api/v1/auth/logout-all` | bearer | 204 — revokes every **other** session, keeps the caller's |
| `GET` | `/api/v1/auth/me` | bearer | 200, `UserRead` |
| `GET` | `/api/v1/auth/sessions` | bearer | 200, `SessionListRead` — the caller's live devices |
| `DELETE` | `/api/v1/auth/sessions/{session_id}` | bearer | 204 — 404 if the caller does not own that session |
| `PATCH` | `/api/v1/auth/password` | bearer | 204 — ends every session except the caller's |
| `POST` | `/api/v1/auth/password/forgot` | no | 202 — identical body for a known and an unknown address. 429 `rate_limited` on the same credential budget as login |
| `POST` | `/api/v1/auth/password/reset` | no | 204 — ends **every** session, including the caller's |
| `PATCH` | `/api/v1/users/me` | bearer + `users.write` | 200, `UserRead` |
| `DELETE` | `/api/v1/users/me` | bearer | 204 — requires the account password and `confirm: true` |
| `GET` | `/api/v1/users/` | bearer + admin | 200, `UserRead[]` — a permission-system fixture, not a feature |

The full contract — error codes, request ids, pagination, the 404-not-403 rule on session
revocation, and the rules every future endpoint must follow — is in
[`docs/api-conventions.md`](docs/api-conventions.md).

---

## Troubleshooting

### The database is unreachable

**Symptom.** `/health` is green, but `/api/v1/health` reports
`"status": "degraded"` with `"database": {"status": "unavailable"}`, and auth calls fail.
That is by design: `/health` is liveness and must stay green while PostgreSQL is down, so
a dependency failure does not trigger a restart loop.

**Diagnose.**

```bash
# from the repository root
backend/.venv/bin/python scripts/wait_for_db.py --timeout 10
```

The script classifies the failure and tells you what to change. The common causes are a
PostgreSQL that is not running, `POSTGRES_USER`/`POSTGRES_PASSWORD` in `.env` not
matching the server's own role, a `DATABASE_URL` pointing at `localhost` instead of
`127.0.0.1`, or a schema that has never been migrated.

The endpoint itself will not hang while it works this out: the probe in
`backend/app/db/session.py` wraps its `SELECT 1` in `asyncio.timeout` with
`DB_PROBE_TIMEOUT_SECONDS` (3 s by default), so a filtered port or a wedged server is
reported as `"status": "degraded"` — a real answer — instead of blocking the response until
the OS TCP timeout gives up. `DB_POOL_TIMEOUT` does not help here: it bounds waiting for a
free connection from the pool, not the handshake behind it. If your probes fail at exactly
`DB_PROBE_TIMEOUT_SECONDS`, the budget is the right thing to raise in `.env`.

**Fix, in order:**

```bash
docker compose up -d postgres                     # or start your local PostgreSQL
cd backend && ../backend/.venv/bin/python -m alembic upgrade head
```

Confirm with `http://localhost:8000/api/v1/health` — `database.status` must read
`connected`.

### Port already in use

| Port | Held by | Fix |
| --- | --- | --- |
| 5432 | a native PostgreSQL, or an older NEXUS stack | move the native one (`postgresql-<n> start`), or set `POSTGRES_PORT` in `.env` — it is the host side of the published mapping |
| 8000 | a previous backend, or `make up` still running | `docker compose down`, or set `NEXUS_PORT` in `.env` for a host run. The Compose mapping is a literal `8000:8000`, so change the mapping or stop the stack |
| 5173 | another Vite server | Vite runs with `strictPort: true`, so it exits rather than picking a different port — stop the other process |
| 4173 | `npm run preview` already running | stop it, or change `preview.port` in `frontend/vite.config.ts` |

### `InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode`

You started the backend with `uvicorn app.main:app`. That works on Linux and macOS and
starts on Windows, but psycopg's async driver cannot run on asyncio's Windows default
`ProactorEventLoop`, so every database operation fails.

The loop is selected in exactly one place, `backend/app/core/event_loop.py`, and applied
by `backend/run.py`, by `migrations/env.py` and by `tests/conftest.py`. Start the server
with:

```bash
cd backend && python run.py
```

### The application hangs on startup instead of failing

`DATABASE_URL` or `POSTGRES_HOST` contains `localhost`. On Windows that resolves to
`::1`, which psycopg cannot connect to, so the TCP connect never fails — it just waits.
Replace `localhost` with `127.0.0.1`.

### `database "nexus_test" does not exist`

The PostgreSQL image ships only `postgres`, `template0` and `template1`. The test
database is created for you — by `tests/conftest.py` when the suite runs, or explicitly:

```bash
# from the repository root
backend/.venv/bin/python scripts/create_test_database.py   # or: make test-db
```

The script only creates what is missing. `--drop` (or `make test-db TEST_DB_FLAGS=--drop`)
recreates the database and destroys whatever it holds, so it is never the default.

If `TEST_DATABASE_URL` points at the same database as `DATABASE_URL`, the script refuses
to run: the suite truncates tables, and the development database must never be that
database.

### `Settings` fails validation

`SECRET_KEY must be set to a strong random value when ENVIRONMENT=production.` — or
`DEBUG must be false when ENVIRONMENT=production.` Both are raised from
`app/core/config.py` at import time.

A third validator refuses to build `Settings` at all when the four productivity
weights do not sum to 100:

```text
The analytics productivity weights must sum to 100; they sum to 95.0
(analytics_productivity_weight_completion=30.0, ..._deadline=25.0,
..._consistency=20.0, ..._focus=20.0).
```

This is deliberate, and it is worth knowing before you retune the score. The
productivity score is presented as a percentage, so the weights are its
denominators: a set summing to 90 would report a "80/100" that is really
"80/90", and one summing to 120 would report a score of 100 having awarded 120
points. There is no silent renormalisation, because hiding that the configured
numbers were wrong would make the scale unarguable. `Settings` is constructed
once at import time, so a set that does not add up takes the process down rather
than serving analytics whose formula does not hold — you will see it on startup,
not in a dashboard. To retune the score, change one weight and give the
difference to another.

### Hot reload is not working

`NEXUS_RELOAD` defaults to `false` in `run.py`, so set `NEXUS_RELOAD=true` in `.env` for
local development. The Compose backend forces it to `false` on purpose.

### `docker compose up` fails before anything starts

Run the static check, from the repository root:

```bash
backend/.venv/bin/python -m pip install pyyaml     # PyYAML is deliberately not a dependency
backend/.venv/bin/python scripts/verify_compose.py
```

It reports the three services and their build contexts, the bind-mounted host paths, and
that the 14 `${VAR}` the compose file interpolates are all documented in `.env.example`.
Add `--strict` to also list `.env.example` entries compose never uses. It exits non-zero on
any failure. None of that tells you whether the images build or the stack comes up.

Note that `docker-compose.yml` was authored on a machine without Docker, and it has never
been executed by `docker compose` — Docker is still not installed in this development
environment. The YAML is structurally validated by that script; treat the first real run as
untested and go straight to `docker compose logs -f`.

---

## Roadmap

Phase numbers in the module column are the ones recorded in
`frontend/src/features/modules/catalog.ts`, and the state column is what the code does
today.

| Phase | Module | State | Adds |
| --- | --- | --- | --- |
| 1 | Foundation | Done | Repo, stack, auth, migrations, design system, health, docs, tests |
| 1 | Dashboard, Settings | Live | Service health card, theme and session preferences |
| 2 | Identity and security | Done | Persistent device sessions, password policy / change / reset, role-based permissions, security audit trail, five-tab settings |
| 2 | Projects, Tasks | Live | The execution layer: outcomes, projects, tasks, triage views |
| 3 | Planner | Live | Weekly capacity, time blocks, focus log |
| 4 | Knowledge | Live | Linked notes, concepts, resources, bookmarks |
| 4 | Search | Placeholder | Unified cross-module retrieval index |
| 5 | Analytics | Live | Traceable metrics derived from real records |
| 6 | Developer | **Delivered in Phase 8** | Local git repository analysis, work attribution |
| 7 | Risks, Recommendations | Live | The risk register and the drafts derived from it |
| 8 | Learning | **Delivered in Phase 9** | Goals, tracked skills, skill gaps |
| 9 | Career | **Delivered in Phase 9** | Profile, dated records, portfolio evidence |
| 9 | AI Assistant | Placeholder | Grounded local-LLM answers via Ollama, proposed actions |
| 10 | Experiments | Placeholder | Hypothesis, bounded scope, keep-or-kill verdict |

Phase 2 was *infrastructure*, not product surface: it made the accounts behind the shell
real and added no module. Note also that the module phases above are the ones recorded in
`frontend/src/features/modules/catalog.ts` and describe when each **module** ships — they
are not the same axis as the platform work recorded in the
[Status](#status-phases-1-through-9-are-delivered) table, which is why both carry a
"Phase 2".

Behind those modules sit infrastructure seams that are described in the extension-roadmap
section of [`docs/architecture.md`](docs/architecture.md): Redis (replacing the in-process
access-token revocation store), an audit-log pruning job (the retention setting exists; the
job does not), background workers, a local model registry, and Ollama-backed local LLM
features. Two seams are no longer seams: git repository analysis shipped in Phase 8 and reads
local work trees through the `git` CLI, and the ML training pipeline has shipped in
[Phase 10](#phase-10--ml-training) under `backend/ml/`. Note that the `10 / Experiments`
row above is the *module* axis, not the platform axis — the Experiments page is still a
placeholder, while the Phase 10 ML pipeline is real work.

---

## Further documentation

| Document | Contents |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | Layering, request lifecycle, auth and session design, RBAC, the audit trail, password policy and reset, configuration, logging, database strategy, extension roadmap, decisions and rationale |
| [`docs/development.md`](docs/development.md) | Clean-machine setup, the daily loop, migrations, adding a repository, service, permission-guarded endpoint, audit event, page, settings panel or design-system primitive, testing conventions, code conventions, the pre-pull-request checklist, and the verified baseline (including what was *not* run) |
| [`docs/api-conventions.md`](docs/api-conventions.md) | Base URL and versioning, health endpoints, the full endpoint catalogue, authentication (tokens, sessions, the password policy, permissions, password reset), the error envelope and its full code table, request-id correlation, pagination, the checklist every endpoint must satisfy, and the conventions Phases 8 and 9 established |
| [`docs/specifications/phase-8-developer-report.md`](docs/specifications/phase-8-developer-report.md) | What Phase 8 shipped, the four git tables, the 14 routes, `developer_features.v1`, the tests actually executed, the two defects three agents independently reported, and the known repo-scan limitations |
| [`docs/specifications/phase-9-learning-career-report.md`](docs/specifications/phase-9-learning-career-report.md) | What Phase 9 shipped, the six learning/career tables, the 33 routes, `learning_features.v1` and `career_features.v1`, the tests actually executed, and the contract disagreements |
| [`docs/specifications/phase-10-architecture.md`](docs/specifications/phase-10-architecture.md) | Where the Phase 10 `backend/ml/` package sits, its two interpreters, and its execution boundary |
| [`docs/specifications/phase-10-training.md`](docs/specifications/phase-10-training.md) | The Phase 10 routing classifier and its evaluation, with what was executed |
| [`docs/specifications/phase-10-report.md`](docs/specifications/phase-10-report.md) | The final Phase 10 run report: the corpus, the metrics, checkpoint/resume evidence, and the limitations |
| [`docs/specifications/README.md`](docs/specifications/README.md) | The authoritative specifications for Phases 3–10 — projects and tasks, planner and scheduling, knowledge base, analytics, risk and recommendations, developer intelligence, learning and career, ML training — with the dependency chain each phase requires and the standing rules that apply to all of them |