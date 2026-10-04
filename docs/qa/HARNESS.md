# QA harness — how to actually exercise this app in this environment

Read this before you write anything. It describes what runs here, what does not, and
the rules that keep 28 simultaneous agents from trampling each other.

## 1. What this machine actually has

Measured, not assumed:

| Capability | Status | Proof |
| --- | --- | --- |
| Python 3.13 venv at `backend/.venv/` | ✅ works | `backend/.venv/Scripts/python.exe --version` |
| FastAPI app imports with **no database running** | ✅ works | `cd backend && .venv/Scripts/python.exe -c "from app.main import app; print(len(app.openapi()['paths']))"` → `145` paths / `192` operations |
| Pure-unit pytest modules (no DB fixture) | ✅ works | e.g. `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ml_actions.py -q` |
| DB-backed pytest modules (FastAPI client fixtures) | ❌ **error out** | no PostgreSQL and no Docker on this host |
| PostgreSQL | ❌ absent | `docker` not on PATH, nothing listening on 5432 |
| Docker | ❌ absent | `docker: command not found` |
| Frontend `npx vitest run <file>` | ✅ works | renders real components in jsdom |
| `npm run typecheck`, `npm run build`, `npm run lint` | ✅ works | no DB needed |
| Playwright / real browser | ❌ absent | not installed, do not install |

**Consequence, stated plainly:** you cannot spin the server up against a live database.
Your evidence comes from three real sources, in descending order of strength:

1. **Executed runs** — a command whose output you paste into the report. A vitest file
   that renders the actual page, a pytest module that actually ran, a real
   `npm run typecheck`, a real `npx eslint`, a real OpenAPI dump, a real `git log` on a
   fixture repository.
2. **Executable probes you write and delete** — a temporary vitest file that mounts the
   real component with realistic fixtures and asserts what a user would see.
3. **Close reading of the real source** — line-accurate, cross-checked against the tests
   that already exist and against the API client the frontend actually calls.

A report made only of item 3, speculating about behaviour you never observed, is not
acceptable. Every finding must carry at least one piece of concrete evidence, and you must
say which kind it is.

## 2. Running things

Paths are relative to the repository root `E:\Nexo`.

```bash
# Backend unit tests (DB-free modules only)
cd backend && .venv/Scripts/python.exe -m pytest tests/test_ml_actions.py -q
cd backend && .venv/Scripts/python.exe -m pytest tests/test_X.py -q -k "name_fragment"

# Backend lint over the files you own
cd backend && .venv/Scripts/python.exe -m ruff check app/...
cd backend && .venv/Scripts/python.exe -m ruff format --check app/...

# The real API surface — 192 operations, no DB needed
cd backend && .venv/Scripts/python.exe -c "
from app.main import app
import json
spec = app.openapi()
print(json.dumps(sorted(spec['paths']), indent=1))
print(json.dumps(spec['components']['schemas'], indent=1)[:20000])
"

# Frontend — render one real test file
cd frontend && npx vitest run src/pages/tasks-page.test.tsx --reporter=basic
cd frontend && npx vitest run src/features/assistant --reporter=basic

# Frontend static gates
cd frontend && npm run typecheck
cd frontend && npm run lint
cd frontend && npm run build
```

Timestamps: `date` works. Git history: `git log -- <path>` works.

## 3. Rules — these are not optional

1. **You are a tester. You do not fix.** The only file you create is your report. Do not
   edit anything under `backend/app/`, `frontend/src/`, `docs/specifications/`, or any
   test file. A second swarm fixes what you report; if you start editing, you will collide
   with them and destroy other agents' work.
2. **Never run a full test suite.** 28 agents share this machine. `npm test`,
   `pytest` with no arguments, and `npm run build` are all too heavy to run concurrently.
   Run **single files** or `-k`-filtered selections, at most 3 invocations, each with a
   timeout of 240 s.
3. **Never install anything.** No `pip install`, no `npm install`, no `npx <new-tool>`
   that downloads, no `docker`. If a tool is missing, report the missing capability as a
   finding with its impact instead of working around it.
4. **Never touch git.** No `commit`, `checkout`, `stash`, `reset`, `push`, `clean`.
   Read-only git (`log`, `show`, `diff`, `ls-files`) is fine.
5. **Never write outside your phase.** Two agents per phase, one report each, named
   exactly as told in your task. If you find something outside your phase, record it in a
   section called `## Out-of-phase observations` at the bottom of your report and keep it
   short — do not go read that module deeply.
6. **Temporary probes must be deleted.** If you need a custom vitest file to observe real
   behaviour, write it at `frontend/src/__probe.<your-slug>.probe.test.tsx`, run it, then
   delete it before you finish. Verify with `ls` that it is gone.
7. **Do not fabricate.** Never invent a stack trace, a test result, or a line number. If
   you could not verify something, write `UNVERIFIED — reason` in the evidence field. A
   short honest report beats a long speculative one.
8. **Finish the whole phase.** Look at the entire surface you have been given — every page,
   every route, every service, every state (loading, empty, error, success, forbidden).
   Do not stop at the first three findings.

## 4. What counts as a bug, in this codebase's terms

- **Correctness** — wrong result, wrong arithmetic, wrong time/date, lost or duplicated
  data, a state change that does not survive a reload, a failure that is swallowed.
- **Contract mismatch** — the frontend calls a route, field, enum value, or status code
  the backend does not actually have. Cross-check `frontend/src/lib/api-client.ts` and the
  feature clients against the real OpenAPI dump. This class is under-found; hunt it.
- **Error and empty handling** — a page that renders nothing, an unhandled rejection, a
  spinner that never resolves, a `console.error` the user can trigger, a failed save that
  silently reports success.
- **Security and privacy** — tenant isolation gaps (a user can read another's data),
  permission bypass, secrets or PII in logs, unvalidated input reaching a shell or a path,
  destructive actions without confirmation.
- **Accessibility** — WCAG 2.2 A/AA: labels, roles, keyboard reachability and traps, focus
  management, contrast, live-region announcements, form error association. Real, specific,
  and cited to a file and line.
- **UI/UX** — information hierarchy, layout breakage at narrow and wide widths, empty and
  error states that do not exist, confusing labels, destructive actions one click away,
  misleading copy, inconsistent affordances between screens, formatting and number/date
  presentation, no confirmation after a destructive action, focus lost after a modal.
- **Documentation drift** — `README.md`, `docs/specifications/*.md` and `docs/architecture.md`
  making a claim that the code contradicts. There is a `tests/test_documentation_claims.py`
  module; the drift is usually in claims that module does not cover.
- **Performance** — an N+1 query, an unbounded list, work done per render that should be
  memoised, a request fired in a render path that can loop.

Not bugs: taste, missing features that no specification asked for, refactoring
preferences, and anything you cannot back with evidence.

## 5. Report format

Write exactly one file, at the path your task gives you:

```markdown
# Phase <N> QA — <Your role name>

**Scope covered:** <what you actually examined — list the files/areas>
**Runs executed:** <the commands, with the real result line from each>

## Findings

### <ID> — <S1|S2|S3|S4> — <one-line title>

- **Component:** `path/to/file.tsx:120` (and any other location)
- **Category:** correctness | contract | error-handling | a11y | ui-ux | security | perf | docs
- **Evidence:** <command + real output, or the probe you ran and what it printed, or the
  exact source lines you read>
- **Steps to reproduce:** <numbered, concrete>
- **Expected:** …
- **Actual:** …
- **Impact:** <what breaks for the user; say "no user-visible impact" if none>
- **Suggested fix:** <specific, and honest about the trade-off>

## Checked and found correct

<short list of the things you actively exercised that behaved properly — this is what
stops the fixer from re-investigating work you already did>

## Out-of-phase observations

<optional, brief>
```

Severity scale:

- **S1** — data loss, data corruption, cross-tenant leak, auth bypass, crash, or a
  primary flow that cannot be completed at all.
- **S2** — wrong behaviour a normal user hits, broken contract, missing error/empty
  state, a11y blocker on a primary flow.
- **S3** — real but minor: confusing copy, small layout break, edge case, slow but works.
- **S4** — nit; still record it, clearly marked as optional.

Aim for signal, not volume. Ten verified findings beat forty hedged ones. If a phase is
genuinely clean, say so and show the runs that prove it.